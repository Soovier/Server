"""The editor: FFmpeg does all cutting, reframing and caption burn-in.

Why FFmpeg and not DaVinci/CapCut: neither can be driven unattended (CapCut
has no API; DaVinci scripting needs the Studio GUI open). FFmpeg is free,
runs headless on a schedule, and produces the same result every time.

Output: 1080x1920 (9:16), H.264 + AAC, which Instagram Reels, Facebook Reels
and YouTube Shorts all accept.
"""
from __future__ import annotations

import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

W, H = 1080, 1920


@dataclass
class Word:
    start: float
    end: float
    text: str


def ffmpeg_bin() -> str:
    path = shutil.which("ffmpeg")
    if not path:
        raise RuntimeError("FFmpeg is not installed. See SETUP.md step 1.")
    return path


def _ass_time(t: float) -> str:
    t = max(t, 0)
    h = int(t // 3600)
    m = int((t % 3600) // 60)
    s = t % 60
    return f"{h}:{m:02d}:{s:05.2f}"


def _esc(text: str) -> str:
    return text.replace("\\", "").replace("{", "(").replace("}", ")").replace("\n", " ")


def build_ass(words: list[Word], hook: str, duration: float, words_per_line: int = 3,
              brand: str = "") -> str:
    """Captions in the bold, centered, few-words-at-a-time style short-form uses.

    `words` are relative to the clip start.
    """
    header = f"""[Script Info]
ScriptType: v4.00+
PlayResX: {W}
PlayResY: {H}
WrapStyle: 0

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Cap,Arial Black,86,&H00FFFFFF,&H000000FF,&H00000000,&H64000000,-1,0,0,0,100,100,0,0,1,6,2,2,60,60,520,1
Style: Hot,Arial Black,86,&H0000E5FF,&H000000FF,&H00000000,&H64000000,-1,0,0,0,100,100,0,0,1,6,2,2,60,60,520,1
Style: Hook,Arial Black,70,&H00FFFFFF,&H000000FF,&H00000000,&HB4A020F0,-1,0,0,0,100,100,0,0,3,18,0,8,80,80,170,1
Style: Brand,Arial,38,&H80FFFFFF,&H000000FF,&H00000000,&H00000000,-1,0,0,0,100,100,2,0,1,2,0,2,40,40,90,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    lines = []
    if hook:
        lines.append(f"Dialogue: 2,{_ass_time(0)},{_ass_time(min(3.5, duration))},Hook,,0,0,0,,"
                     "{\\fad(120,200)\\fscx85\\fscy85\\t(0,160,\\fscx100\\fscy100)}" + _esc(hook.upper()))
    if brand:
        lines.append(f"Dialogue: 1,{_ass_time(0)},{_ass_time(duration)},Brand,,0,0,0,,{_esc(brand)}")
    # Group words into short chunks; highlight the current word in yellow.
    for i in range(0, len(words), words_per_line):
        chunk = words[i:i + words_per_line]
        for j, w in enumerate(chunk):
            end = chunk[j + 1].start if j + 1 < len(chunk) else w.end
            parts = []
            for k, x in enumerate(chunk):
                t = _esc(x.text.strip().upper())
                parts.append("{\\c&H00E5FF&}" + t + "{\\c&HFFFFFF&}" if k == j else t)
            pop = "{\\fscx112\\fscy112\\t(0,90,\\fscx100\\fscy100)}" if j == 0 else ""
            lines.append(f"Dialogue: 0,{_ass_time(w.start)},{_ass_time(max(end, w.start + 0.05))},Cap,,0,0,0,,"
                         + pop + " ".join(parts))
    return header + "\n".join(lines) + "\n"


def _punch_expr(punches: list[float], amount: float = 0.12, half: float = 0.35) -> str:
    """Zoom factor over time: a quick, smooth punch-in around each moment."""
    if not punches:
        return "1"
    bumps = "+".join(f"max(0,1-abs(t-{p:.2f})/{half})" for p in punches[:8])
    return f"(1+{amount}*min(1,{bumps}))"


def render_command(src: Path, out: Path, start: float, end: float, ass_path: Path | None,
                   mode: str = "blur", facecam=None, bars=None, punches: list[float] | None = None) -> list[str]:
    """Build the FFmpeg command.

    mode 'stack': facecam on top, gameplay below (needs a facecam box; the
                  standard streamer layout for Shorts).
    mode 'blur':  full frame centered over a blurred, zoomed copy (keeps the
                  whole picture; best for pure gameplay).
    mode 'crop':  center crop to 9:16 (best for talking-head / IRL streams).
    facecam/bars: analysis.Box regions (fractions of the frame).
    punches:      seconds (relative to the clip) to zoom in on big moments.
    """
    dur = max(end - start, 0.5)
    pre = (bars.ffmpeg_crop() + ",") if bars else ""
    if mode == "stack" and facecam:
        cam_h = int(H * 0.36) // 2 * 2
        game_h = H - cam_h
        vf = (f"[0:v]{pre}split=2[a][b];"
              f"[a]{facecam.ffmpeg_crop()},scale={W}:{cam_h}:force_original_aspect_ratio=increase,crop={W}:{cam_h}[cam];"
              f"[b]scale=-2:{game_h},crop={W}:{game_h}[game];"
              f"[cam][game]vstack=inputs=2,setsar=1")
    elif mode == "crop":
        vf = f"[0:v]{pre}crop=ih*9/16:ih,scale={W}:{H},setsar=1"
    else:
        vf = (f"[0:v]{pre}split=2[bg][fg];"
              f"[bg]scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H},boxblur=24:2,eq=brightness=-0.08[bgb];"
              f"[fg]scale={W}:-2[fgs];"
              f"[bgb][fgs]overlay=(W-w)/2:(H-h)/2,setsar=1")
    if punches:
        z = _punch_expr(punches)
        vf += (f",scale=w='trunc({W}*{z}/2)*2':h='trunc({H}*{z}/2)*2':eval=frame"
               f",crop={W}:{H}:(iw-{W})/2:(ih-{H})/2")
    if ass_path:
        # subtitles filter path needs escaping of ':' and '\' on Windows
        p = str(ass_path).replace("\\", "/").replace(":", "\\:")
        vf += f",ass='{p}'"
    vf += "[v]"
    fade_out = max(dur - 0.25, 0)
    af = (f"highpass=f=70,acompressor=threshold=0.125:ratio=3:attack=5:release=120:makeup=1.5,"
          f"loudnorm=I=-14:TP=-1.5:LRA=11,afade=t=in:d=0.05,afade=t=out:st={fade_out:.2f}:d=0.25")
    return [
        ffmpeg_bin(), "-y", "-hide_banner", "-loglevel", "error",
        "-ss", f"{start:.3f}", "-i", str(src), "-t", f"{dur:.3f}",
        "-filter_complex", vf, "-map", "[v]", "-map", "0:a?",
        "-c:v", "libx264", "-preset", "medium", "-crf", "20", "-pix_fmt", "yuv420p", "-r", "30",
        "-c:a", "aac", "-b:a", "160k", "-ar", "48000",
        "-af", af,
        "-movflags", "+faststart",
        str(out),
    ]


def render_clip(src: Path, out: Path, start: float, end: float, words: list[Word], hook: str = "",
                mode: str = "blur", brand: str = "", facecam=None, bars=None,
                punches: list[float] | None = None) -> Path:
    out.parent.mkdir(parents=True, exist_ok=True)
    ass = out.with_suffix(".ass")
    ass.write_text(build_ass(words, hook, end - start, brand=brand), encoding="utf-8")
    cmd = render_command(src, out, start, end, ass, mode, facecam=facecam, bars=bars, punches=punches)
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        raise RuntimeError(f"FFmpeg failed: {proc.stderr[-800:]}")
    return out


def thumbnail(video: Path, out: Path, at: float = 1.0) -> Path:
    subprocess.run([ffmpeg_bin(), "-y", "-loglevel", "error", "-ss", f"{at}", "-i", str(video),
                    "-frames:v", "1", "-vf", "scale=360:-2", str(out)], check=True)
    return out


def probe_duration(path: Path) -> float:
    ffprobe = shutil.which("ffprobe") or "ffprobe"
    r = subprocess.run([ffprobe, "-v", "error", "-show_entries", "format=duration", "-of",
                        "default=nw=1:nk=1", str(path)], capture_output=True, text=True)
    try:
        return float(r.stdout.strip())
    except ValueError:
        return 0.0
