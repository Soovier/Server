"""Turn a long source video into ranked short clips.

The 12 stages, in order:
   1. Footage analysis + scene detection   analysis.scene_cuts, audio_energy
   2. Viral moment + reaction detection    transcript hype words, analysis.reaction_bursts
   3. Highlight scoring + selection        score_windows, pick_moments
   4. Smart clip extraction                snap_to_speech, analysis.snap_to_cut
   5. AI reframing                         analysis.detect_facecam / black_bars -> stack | blur | crop
   6. Hooks + captions                     ai_refine (Claude), default_copy
   7. Subtitle sync                        word-level timings from faster-whisper -> editor.build_ass
   8. Motion + visual enhancement          zoom punch-ins on reaction peaks, caption pop
   9. Audio optimisation                   high-pass, compression, -14 LUFS loudness
  10. Quality control                      qc.check_clip
  11. Copyright risk detection             analysis.music_ratio + Claude -> copyright_risk
  12. Export                               1080x1920 H.264/AAC, thumbnail, into the review queue
"""
from __future__ import annotations

import array
import json
import math
import re
import shutil
import subprocess
import time
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx

from . import analysis, qc
from .config import settings
from .db import DB, utcnow
from .editor import Word, ffmpeg_bin, probe_duration, render_clip, thumbnail

HYPE_WORDS = {
    "no way", "oh my god", "omg", "what", "bro", "let's go", "lets go", "insane", "crazy", "clip that",
    "clip it", "dude", "wait", "holy", "yo", "bruh", "wtf", "are you serious", "i can't", "lmao", "haha",
}


@dataclass
class Moment:
    start: float
    end: float
    score: float
    hook: str = ""
    caption: str = ""
    hashtags: str = ""
    words: list[Word] = field(default_factory=list)
    ai_risk: float | None = None

    @property
    def text(self) -> str:
        return " ".join(w.text.strip() for w in self.words)


# ---------------- download ----------------

def download(url: str, dest_dir: Path, max_height: int = 1080, on_progress=None) -> Path:
    """Download with yt-dlp (Twitch, YouTube, Kick and most sites).
    on_progress(percent, eta_seconds) is called as it goes, so the app can show time left."""
    ytdlp = shutil.which("yt-dlp")
    dest_dir.mkdir(parents=True, exist_ok=True)
    tmpl = str(dest_dir / "%(extractor)s_%(id)s.%(ext)s")
    fmt = f"bv*[height<={max_height}]+ba/b[height<={max_height}]/b"
    if ytdlp:
        cmd = [ytdlp, "-f", fmt, "--merge-output-format", "mp4", "-o", tmpl, "--no-playlist",
               "--newline", "--progress", "--progress-template",
               "download:SXMPROG %(progress._percent_str)s %(progress.eta)s",
               "--print", "after_move:filepath", url]
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                                encoding="utf-8", errors="replace")
        lines: list[str] = []
        for line in proc.stdout:  # type: ignore[union-attr]
            line = line.strip()
            if line.startswith("SXMPROG"):
                _report(on_progress, *line.split()[1:3])
            elif line:
                lines.append(line)
                del lines[:-40]
        proc.wait()
        if proc.returncode != 0:
            raise RuntimeError("yt-dlp failed: " + "\n".join(lines)[-600:])
        paths = [l for l in lines if Path(l).suffix and Path(l).exists()]
        return Path(paths[-1] if paths else lines[-1])
    import yt_dlp  # type: ignore

    def hook(d):
        if d.get("status") == "downloading":
            total = d.get("total_bytes") or d.get("total_bytes_estimate") or 0
            pct = f"{100 * d.get('downloaded_bytes', 0) / total:.1f}%" if total else "NA"
            _report(on_progress, pct, str(d.get("eta")))
    with yt_dlp.YoutubeDL({"format": fmt, "outtmpl": tmpl, "merge_output_format": "mp4",
                           "noplaylist": True, "quiet": True, "progress_hooks": [hook]}) as ydl:
        info = ydl.extract_info(url, download=True)
        return Path(ydl.prepare_filename(info)).with_suffix(".mp4")


def _report(cb, pct: str = "", eta: str = "") -> None:
    if not cb:
        return
    try:
        p = float(pct.strip().rstrip("%"))
    except (ValueError, AttributeError):
        p = None
    try:
        e = int(float(eta))
    except (TypeError, ValueError):
        e = None
    try:
        cb(p, e)
    except Exception:
        pass


def eta_text(seconds: int | None) -> str:
    if seconds is None:
        return "working out time left"
    if seconds < 60:
        return f"{seconds}s left"
    if seconds < 3600:
        return f"{seconds // 60}m {seconds % 60:02d}s left"
    return f"{seconds // 3600}h {seconds % 3600 // 60:02d}m left"


# ---------------- signals ----------------

def audio_energy(path: Path, rate: int = 8000) -> list[float]:
    """RMS loudness for each second of audio."""
    proc = subprocess.run([ffmpeg_bin(), "-v", "error", "-i", str(path), "-vn", "-ac", "1", "-ar", str(rate),
                           "-f", "s16le", "-"], capture_output=True)
    if proc.returncode != 0:
        return []
    samples = array.array("h")
    samples.frombytes(proc.stdout[: len(proc.stdout) // 2 * 2])
    out = []
    for i in range(0, len(samples), rate):
        chunk = samples[i:i + rate]
        if not chunk:
            break
        out.append(math.sqrt(sum(s * s for s in chunk) / len(chunk)))
    return out


def transcribe(path: Path) -> list[Word]:
    """Local Whisper transcript with word timestamps. Empty if not installed."""
    try:
        from faster_whisper import WhisperModel  # type: ignore
    except ImportError:
        return []
    model = WhisperModel(settings.whisper_model, device="auto", compute_type="auto")
    segments, _ = model.transcribe(str(path), word_timestamps=True, vad_filter=True)
    words: list[Word] = []
    for seg in segments:
        for w in seg.words or []:
            words.append(Word(float(w.start), float(w.end), w.word))
    return words


def _zscores(xs: list[float]) -> list[float]:
    if not xs:
        return []
    mean = sum(xs) / len(xs)
    sd = math.sqrt(sum((x - mean) ** 2 for x in xs) / len(xs)) or 1.0
    return [(x - mean) / sd for x in xs]


def score_windows(energy: list[float], words: list[Word], duration: float, length: int,
                  step: int = 5, cuts: list[float] | None = None) -> list[tuple[float, float, float]]:
    """Score every window of `length` seconds. Returns (start, end, score)."""
    z = _zscores(energy)
    n = int(duration) if duration else len(z)
    action = analysis.cut_density(cuts or [], n)
    bursts = set(analysis.reaction_bursts(z))
    per_sec_words = [0] * (n + 1)
    hype = [0.0] * (n + 1)
    for w in words:
        i = min(int(w.start), n)
        per_sec_words[i] += 1
    text_by_sec: dict[int, str] = {}
    for w in words:
        text_by_sec.setdefault(min(int(w.start), n), "")
        text_by_sec[min(int(w.start), n)] += " " + w.text.lower()
    for sec, txt in text_by_sec.items():
        hype[sec] = sum(1.0 for h in HYPE_WORDS if h in txt) + (1.0 if "!" in txt else 0) + (0.5 if "?" in txt else 0)

    out = []
    for s in range(0, max(n - length, 0) + 1, step):
        e = s + length
        loud = z[s:e] if z else []
        peak = max(loud) if loud else 0.0
        avg = sum(loud) / len(loud) if loud else 0.0
        # Strong hooks happen early: weight energy in the first 5s.
        opening = sum(loud[:5]) / 5 if len(loud) >= 5 else 0.0
        talk = sum(per_sec_words[s:e]) / length if words else 0.0
        hy = sum(hype[s:e])
        act = sum(action[s:e]) / length
        react = sum(1 for b in bursts if s <= b < e)
        early_react = sum(1 for b in bursts if s <= b < s + 6)
        score = (1.5 * peak + 1.0 * avg + 0.8 * opening + 0.6 * min(talk, 4) + 0.7 * hy
                 + 1.2 * min(act, 1.5) + 0.6 * min(react, 4) + 0.6 * min(early_react, 2))
        out.append((float(s), float(e), score))
    return out


def pick_moments(windows: list[tuple[float, float, float]], k: int, min_gap: float = 30) -> list[Moment]:
    """Top-k non-overlapping windows."""
    chosen: list[Moment] = []
    for s, e, sc in sorted(windows, key=lambda w: w[2], reverse=True):
        if all(e + min_gap <= m.start or s >= m.end + min_gap for m in chosen):
            chosen.append(Moment(s, e, sc))
        if len(chosen) >= k:
            break
    return sorted(chosen, key=lambda m: m.start)


def snap_to_speech(m: Moment, words: list[Word]) -> Moment:
    """Start on a word boundary and end at the end of a sentence where possible."""
    inside = [w for w in words if m.start - 1 <= w.start <= m.end + 3]
    if inside:
        start = inside[0].start
        end = m.end
        for w in inside:
            if w.end >= m.end - 4 and re.search(r"[.!?]$", w.text.strip()):
                end = w.end + 0.3
                break
        m.start, m.end = max(0.0, start - 0.15), end
    m.words = [Word(w.start - m.start, w.end - m.start, w.text) for w in words
               if m.start <= w.start < m.end]
    return m


# ---------------- optional AI pass ----------------

def ai_refine(moments: list[Moment], words: list[Word], title: str, streamer: str,
              campaign_rules: str = "") -> list[Moment]:
    """Ask Claude to rank candidates and write hook + caption. Skipped without a key."""
    if not settings.anthropic_api_key or not moments:
        return moments
    cands = []
    for i, m in enumerate(moments):
        txt = " ".join(w.text.strip() for w in words if m.start <= w.start < m.end)
        cands.append({"i": i, "start": round(m.start), "end": round(m.end), "transcript": txt[:1500]})
    prompt = (
        "You pick viral short-form moments from a stream and write copy for them.\n"
        f"Stream: {title!r} by {streamer!r}.\n"
        + (f"Campaign rules to follow exactly:\n{campaign_rules}\n" if campaign_rules else "")
        + "For each candidate, return JSON objects with: i, keep (bool: is this a strong standalone clip?), "
        "score (0-10), hook (max 7 words, on-screen, punchy, no hashtags), caption (one line, under 120 chars, "
        "credits the streamer), hashtags (3-5, space separated, include #shorts), "
        "copyright_risk (0-1: how likely licensed music, a song being sung, in-game radio, or a film/TV show is "
        "audible from the transcript and context; 0 if it's just people talking and game sounds).\n"
        "Reply with only a JSON array.\n\n" + json.dumps(cands)
    )
    try:
        r = httpx.post("https://api.anthropic.com/v1/messages", timeout=90, headers={
            "x-api-key": settings.anthropic_api_key, "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        }, json={"model": settings.anthropic_model, "max_tokens": 2000,
                 "messages": [{"role": "user", "content": prompt}]})
        r.raise_for_status()
        text = "".join(b.get("text", "") for b in r.json().get("content", []))
        data = json.loads(text[text.find("["): text.rfind("]") + 1])
    except Exception:
        return moments
    out = []
    for d in data:
        i = d.get("i")
        if not isinstance(i, int) or i >= len(moments) or not d.get("keep", True):
            continue
        m = moments[i]
        m.score = float(d.get("score", m.score))
        m.hook = str(d.get("hook", ""))[:60]
        m.caption = str(d.get("caption", ""))[:300]
        m.hashtags = str(d.get("hashtags", ""))[:200]
        try:
            m.ai_risk = max(0.0, min(1.0, float(d.get("copyright_risk"))))
        except (TypeError, ValueError):
            pass
        out.append(m)
    return out or moments


def _hype_phrase(words: list[Word], size: int = 6) -> str:
    """The most exciting few words said in the clip (hype words, shouting, '!')."""
    if not words:
        return ""
    toks = [w.text.strip() for w in words]

    def heat(i: int) -> float:
        t = toks[i]
        starts_phrase = any(" ".join(x.lower().strip("!?.,") for x in toks[i:i + len(h.split())]) == h
                            for h in HYPE_WORDS)
        return (2.0 * starts_phrase
                + 1.0 * ("!" in t) + 1.0 * (t.isupper() and len(t) > 1))
    scores = [heat(i) for i in range(len(toks))]
    best = max(range(max(1, len(toks) - size + 1)), key=lambda i: (sum(scores[i:i + size]), scores[i], -i))
    phrase = " ".join(toks[best:best + size]).strip(" ,.")
    return phrase[:48]


def default_copy(m: Moment, streamer: str, title: str) -> Moment:
    if not m.hook:
        m.hook = _hype_phrase(m.words) or (title[:40] if title else "WAIT FOR IT")
    if not m.caption:
        line = m.hook.rstrip(".") if m.hook and m.hook != "WAIT FOR IT" else (title[:80] or "Wait for it")
        credit = f" 🎥 {streamer}" if streamer else ""
        m.caption = f"{line[:1].upper()}{line[1:]} 😳{credit} | Follow for more clips"[:200]
    if not m.hashtags:
        tag = re.sub(r"[^a-zA-Z0-9]", "", streamer or "")
        m.hashtags = " ".join(x for x in (f"#{tag}" if tag else "", "#clips", "#stream", "#shorts") if x)
    return m


# ---------------- main entry ----------------

# One video at a time, whether the scheduler or the import queue asked for it.
CLIP_LOCK = threading.Lock()


def clip_source(db: DB, source_id: int, renderer=render_clip) -> list[int]:
    src = db.one("SELECT * FROM sources WHERE id=?", (source_id,))
    if not src:
        return []
    camp = db.one("SELECT * FROM campaigns WHERE id=?", (src["campaign_id"],)) if src["campaign_id"] else None
    want = int(src.get("clips_wanted") or settings.clips_per_source)

    def step(msg: str, **extra) -> None:
        db.update("sources", source_id, progress=msg, **extra)
    try:
        path = Path(src["local_path"]) if src.get("local_path") and Path(src["local_path"]).exists() else None
        if not path:
            step("Downloading the video", status="downloading", error=None)
            last = [0.0]

            def dl_progress(pct, eta):
                if time.time() - last[0] < 2:
                    return
                last[0] = time.time()
                step(f"Downloading {pct:.0f}% · {eta_text(eta)}" if pct is not None else "Downloading the video")
            path = download(src["url"], settings.media_dir / "sources", on_progress=dl_progress)
            db.update("sources", source_id, local_path=str(path), status="downloaded")
        step("Finding the best moments", status="clipping", error=None)
        duration = probe_duration(path) or float(src.get("duration_s") or 0)
        if not src.get("duration_s") and duration:
            db.update("sources", source_id, duration_s=duration)
        step("Listening for words (captions)")
        words = transcribe(path)
        step("Measuring hype and reactions")
        energy = audio_energy(path)
        energy_z = _zscores(energy)
        short_source = duration <= settings.clip_max_seconds + 5
        if not short_source:
            step("Detecting scene changes")
        cuts = [] if short_source else analysis.scene_cuts(path)
        if short_source:
            moments = [Moment(0.0, duration, 1.0)]
            moments = [snap_to_speech(moments[0], words) if words else moments[0]]
            if not words:
                moments[0].words = []
        else:
            length = max(settings.clip_min_seconds, min(45, settings.clip_max_seconds))
            windows = score_windows(energy, words, duration, length, cuts=cuts)
            moments = pick_moments(windows, want * 2)
            moments = [snap_to_speech(m, words) for m in moments]
            for m in moments:
                cut_start = analysis.snap_to_cut(m.start, cuts)
                if cut_start != m.start:  # open on a clean cut; re-time captions
                    m.start = cut_start
                    m.words = [Word(w.start - m.start, w.end - m.start, w.text) for w in words
                               if m.start <= w.start < m.end]
                m.end = min(m.end, m.start + settings.clip_max_seconds)  # keep inside platform limits
                m.words = [w for w in m.words if w.start < m.end - m.start]
        step("Writing hooks and captions")
        moments = ai_refine(moments, words, src["title"] or "", src["streamer"] or "",
                            camp["rules"] if camp else "")
        moments = sorted(moments, key=lambda m: m.score, reverse=True)[:want]
        bursts = analysis.reaction_bursts(energy_z)
        fixed_cam = analysis.parse_box(settings.facecam_box)

        ids = []
        for m in moments:
            default_copy(m, src["streamer"] or "", src["title"] or "")
            if camp and camp.get("required_tags"):
                m.hashtags = (m.hashtags + " " + camp["required_tags"]).strip()
            # Framing: facecam on top when there is one, else blur (or what you set).
            step(f"Editing clip {len(ids) + 1} of {len(moments)}")
            mode, cam = (src.get("layout") or settings.reframe_mode), None
            if mode in ("auto", "stack"):
                cam = fixed_cam or analysis.detect_facecam(path, m.start, m.end)
                mode = "stack" if cam else "blur"
            bars = analysis.black_bars(path, m.start + 1)
            punches = [b - m.start for b in bursts if m.start + 0.8 <= b <= m.end - 1.0] \
                if settings.zoom_punches else []
            risk = analysis.copyright_risk(analysis.music_ratio(energy_z, words, m.start, m.end), m.text, m.ai_risk)
            clip_id = db.insert("clips", {
                "source_id": source_id, "campaign_id": src["campaign_id"], "start_s": m.start, "end_s": m.end,
                "score": round(m.score, 2), "hook": m.hook, "caption": m.caption, "hashtags": m.hashtags,
                "transcript": m.text, "status": "rendering", "created_at": utcnow(), "layout": mode,
                "copyright_risk": risk,
            })
            out = settings.media_dir / "clips" / f"clip_{clip_id}.mp4"
            try:
                renderer(path, out, m.start, m.end, m.words, m.hook, mode=mode, brand=settings.brand_handle,
                         facecam=cam, bars=bars, punches=punches[:6])
                thumb = thumbnail(out, out.with_suffix(".jpg")) if out.exists() else None
                check = qc.check_clip(out, m.end - m.start, max_s=settings.clip_max_seconds + 1,
                                      copyright_risk=risk)
                db.update("clips", clip_id, video_path=str(out), thumb_path=str(thumb) if thumb else None,
                          status="pending_review", qc_status=check.status, qc_notes=check.text or None)
                ids.append(clip_id)
            except Exception as e:
                db.update("clips", clip_id, status="failed", review_note=str(e)[:500])
        db.update("sources", source_id, status="clipped", clips_made=len(ids),
                  progress=f"Done: {len(ids)} clip{'s' if len(ids) != 1 else ''} ready to review")
        return ids
    except Exception as e:
        db.update("sources", source_id, status="failed", error=str(e)[:500], progress="Failed")
        return []


def next_source(db: DB) -> dict | None:
    return db.one("SELECT id FROM sources WHERE status IN ('new','downloaded') ORDER BY "
                  "priority DESC, (campaign_id IS NULL), id LIMIT 1")


def process_new_sources(db: DB, limit: int = 3) -> str:
    done = made = 0
    for _ in range(limit):
        with CLIP_LOCK:
            r = next_source(db)
            if not r:
                break
            made += len(clip_source(db, r["id"]))
            done += 1
    return f"clipped {done} sources -> {made} clips"
