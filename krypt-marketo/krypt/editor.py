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
        lines.append(f"Dialogue: 2,{_ass_time(0)},{_ass_time(min(3.5, duration))},Hook,,0,0,0,,{_esc(hook.upper())}")
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
            lines.append(f"Dialogue: 0,{_ass_time(w.start)},{_ass_time(max(end, w.start + 0.05))},Cap,,0,0,0,,"
                         + " ".join(parts))
    return header + "\n".join(lines) + "\n"


def render_command(src: Path, out: Path, start: float, end: float, ass_path: Path | None,
                   mode: str = "blur") -> list[str]:
    """Build the FFmpeg command.

    mode 'blur': full frame centered over a blurred, zoomed copy (keeps the
                 whole picture; best for gameplay + facecam layouts).
    mode 'crop': center crop to 9:16 (best for talking-head / IRL streams).
    """
    dur = max(end - start, 0.5)
    if mode == "crop":
        vf = f"crop=ih*9/16:ih,scale={W}:{H},setsar=1"
    else:
        vf = (f"split=2[bg][fg];"
              f"[bg]scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H},boxblur=24:2,eq=brightness=-0.08[bgb];"
              f"[fg]scale={W}:-2[fgs];"
              f"[bgb][fgs]overlay=(W-w)/2:(H-h)/2,setsar=1")
    if ass_path:
        # subtitles filter path needs escaping of ':' and '\' on Windows
        p = str(ass_path).replace("\\", "/").replace(":", "\\:")
        vf += f",ass='{p}'"
    return [
        ffmpeg_bin(), "-y", "-hide_banner", "-loglevel", "error",
        "-ss", f"{start:.3f}", "-i", str(src), "-t", f"{dur:.3f}",
        "-filter_complex" if mode != "crop" else "-vf", vf,
        "-c:v", "libx264", "-preset", "medium", "-crf", "20", "-pix_fmt", "yuv420p", "-r", "30",
        "-c:a", "aac", "-b:a", "160k", "-ar", "48000",
        "-af", "loudnorm=I=-14:TP=-1.5:LRA=11",
        "-movflags", "+faststart",
        str(out),
    ]


def render_clip(src: Path, out: Path, start: float, end: float, words: list[Word], hook: str = "",
                mode: str = "blur", brand: str = "") -> Path:
    out.parent.mkdir(parents=True, exist_ok=True)
    ass = out.with_suffix(".ass")
    ass.write_text(build_ass(words, hook, end - start, brand=brand), encoding="utf-8")
    cmd = render_command(src, out, start, end, ass, mode)
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
