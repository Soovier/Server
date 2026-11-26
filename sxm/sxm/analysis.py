"""Footage analysis: scene cuts, reaction bursts, facecam position, black bars
and a music (copyright) signal. Everything runs on FFmpeg; face detection uses
OpenCV when it's installed and falls back to a fixed layout when it isn't.
"""
from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

from .editor import Word, ffmpeg_bin


# ---------------- scene detection ----------------

def scene_cuts(path: Path, threshold: float = 0.32, height: int = 160) -> list[float]:
    """Times (seconds) where the picture changes hard: cuts, deaths, explosions,
    menu flashes. Runs on a small, low-frame-rate copy so a long VOD stays quick."""
    proc = subprocess.run([
        ffmpeg_bin(), "-hide_banner", "-nostats", "-i", str(path), "-an",
        "-vf", f"fps=6,scale=-2:{height},select='gt(scene,{threshold})',showinfo", "-f", "null", "-",
    ], capture_output=True, text=True)
    return [float(m) for m in re.findall(r"pts_time:([0-9.]+)", proc.stderr)]


def cut_density(cuts: list[float], n_seconds: int) -> list[float]:
    """Cuts per second, smoothed over 3s. Busy footage = more action."""
    per = [0.0] * (n_seconds + 1)
    for c in cuts:
        i = int(c)
        if 0 <= i <= n_seconds:
            per[i] += 1
    return [sum(per[max(0, i - 1): i + 2]) / 3 for i in range(n_seconds + 1)]


def reaction_bursts(energy_z: list[float], jump: float = 1.8) -> list[int]:
    """Seconds where loudness jumps sharply: screams, laughs, 'NO WAY' moments."""
    return [i for i in range(1, len(energy_z)) if energy_z[i] - energy_z[i - 1] >= jump and energy_z[i] > 1.0]


def snap_to_cut(start: float, cuts: list[float], before: float = 1.5) -> float:
    """Start right on a scene cut if one is just before the planned start, so the
    clip opens clean instead of mid-transition."""
    near = [c for c in cuts if start - before <= c <= start]
    return near[-1] if near else start


# ---------------- framing ----------------

@dataclass
class Box:
    """A region of the frame, as fractions of width/height (0..1)."""
    x: float
    y: float
    w: float
    h: float

    def ffmpeg_crop(self) -> str:
        return f"crop=iw*{self.w:.4f}:ih*{self.h:.4f}:iw*{self.x:.4f}:ih*{self.y:.4f}"


def parse_box(text: str) -> Box | None:
    try:
        x, y, w, h = (float(v) for v in text.split(","))
    except (ValueError, AttributeError):
        return None
    if not (0 <= x < 1 and 0 <= y < 1 and 0 < w <= 1 and 0 < h <= 1):
        return None
    return Box(x, y, w, h)


def detect_facecam(path: Path, start: float, end: float, samples: int = 6) -> Box | None:
    """Find a streamer's facecam: a face that stays in the same corner across the
    clip. Needs OpenCV (pip install opencv-python-headless); returns None without it."""
    try:
        import cv2  # type: ignore
    except ImportError:
        return None
    if not hasattr(cv2, "CascadeClassifier"):  # OpenCV 5 moved it out; requirements pin 4.x
        return None
    cascade = cv2.CascadeClassifier(cv2.data.haarcascades + "haarcascade_frontalface_default.xml")
    cap = cv2.VideoCapture(str(path))
    if not cap.isOpened():
        return None
    hits: list[tuple[float, float, float, float]] = []
    W = H = 0
    for i in range(samples):
        t = start + (end - start) * (i + 0.5) / samples
        cap.set(cv2.CAP_PROP_POS_MSEC, t * 1000)
        ok, frame = cap.read()
        if not ok:
            continue
        H, W = frame.shape[:2]
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        faces = cascade.detectMultiScale(gray, scaleFactor=1.15, minNeighbors=6, minSize=(W // 30, W // 30))
        if len(faces):
            x, y, w, h = max(faces, key=lambda f: f[2] * f[3])
            hits.append((x / W, y / H, w / W, h / H))
    cap.release()
    if len(hits) < max(2, samples // 2):
        return None
    # A facecam doesn't move: require the face centres to stay close together.
    cx = [h[0] + h[2] / 2 for h in hits]
    cy = [h[1] + h[3] / 2 for h in hits]
    if max(cx) - min(cx) > 0.08 or max(cy) - min(cy) > 0.08:
        return None
    fx, fy = sum(cx) / len(cx), sum(cy) / len(cy)
    fw = sum(h[2] for h in hits) / len(hits)
    # Facecam box ~ 3.2x the face width, 16:9, clamped to the frame.
    bw = min(0.45, fw * 3.2)
    bh = bw * (W / H if H else 16 / 9) * 9 / 16
    bx = min(max(fx - bw / 2, 0), 1 - bw)
    by = min(max(fy - bh / 2, 0), 1 - bh)
    return Box(round(bx, 4), round(by, 4), round(bw, 4), round(bh, 4))


def black_bars(path: Path, at: float) -> Box | None:
    """Letterboxing / pillarboxing to trim before reframing."""
    proc = subprocess.run([ffmpeg_bin(), "-hide_banner", "-ss", f"{at:.2f}", "-i", str(path), "-t", "3",
                           "-vf", "cropdetect=24:2:0", "-an", "-f", "null", "-"], capture_output=True, text=True)
    found = re.findall(r"crop=(\d+):(\d+):(\d+):(\d+)", proc.stderr)
    size = re.search(r"Stream.*Video.*?(\d{3,5})x(\d{3,5})", proc.stderr)
    if not found or not size:
        return None
    w, h, x, y = (int(v) for v in found[-1])
    W, H = int(size.group(1)), int(size.group(2))
    if w >= W * 0.97 and h >= H * 0.97:
        return None
    return Box(x / W, y / H, w / W, h / H)


# ---------------- copyright signal ----------------

def music_ratio(energy_z: list[float], words: list[Word], start: float, end: float) -> float:
    """Share of the clip that is loud but has nobody talking: usually music
    (GTA radio, background tracks), the main reason clips get claimed."""
    s, e = int(start), max(int(end), int(start) + 1)
    talking = set()
    for w in words:
        for sec in range(int(w.start), int(w.end) + 1):
            talking.add(sec)
    window = range(s, min(e, len(energy_z)))
    if not window:
        return 0.0
    loud_quiet_mic = sum(1 for i in window if energy_z[i] > -0.2 and i not in talking)
    return round(loud_quiet_mic / len(window), 2)


LYRIC_HINTS = re.compile(r"\b(la la|na na|oh oh|yeah yeah|baby|♪)\b", re.I)


def copyright_risk(music: float, transcript: str, ai_risk: float | None = None) -> float:
    """0..1. Combines the music signal, sung-lyric hints and Claude's read."""
    risk = max(0.0, (music - 0.25) / 0.6)
    if LYRIC_HINTS.search(transcript or ""):
        risk += 0.25
    if ai_risk is not None:
        risk = 0.5 * risk + 0.5 * ai_risk
    return round(min(1.0, risk), 2)
