"""Quality control on every rendered clip before it can auto-post."""
from __future__ import annotations

import json
import re
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from .editor import H, W, ffmpeg_bin


@dataclass
class QCResult:
    status: str = "pass"            # pass | warn | fail
    notes: list[str] = field(default_factory=list)

    def warn(self, msg: str) -> None:
        self.notes.append(msg)
        if self.status == "pass":
            self.status = "warn"

    def fail(self, msg: str) -> None:
        self.notes.append(msg)
        self.status = "fail"

    @property
    def text(self) -> str:
        return "; ".join(self.notes)


def _probe(path: Path) -> dict:
    ffprobe = shutil.which("ffprobe") or "ffprobe"
    r = subprocess.run([ffprobe, "-v", "error", "-show_streams", "-show_format", "-of", "json", str(path)],
                       capture_output=True, text=True)
    try:
        return json.loads(r.stdout or "{}")
    except ValueError:
        return {}


def check_clip(path: Path, expected_s: float, max_s: float = 60, copyright_risk: float = 0.0,
               risk_limit: float = 0.5) -> QCResult:
    q = QCResult()
    if not path.exists() or path.stat().st_size < 10_000:
        q.fail("render missing or empty")
        return q
    info = _probe(path)
    streams = info.get("streams", [])
    v = next((s for s in streams if s.get("codec_type") == "video"), None)
    a = next((s for s in streams if s.get("codec_type") == "audio"), None)
    dur = float((info.get("format") or {}).get("duration") or 0)
    if not v:
        q.fail("no video stream")
        return q
    if (int(v.get("width", 0)), int(v.get("height", 0))) != (W, H):
        q.fail(f"wrong size {v.get('width')}x{v.get('height')}")
    if not a:
        q.fail("no audio")
    if dur > max_s + 0.5:
        q.fail(f"too long for Shorts/Reels ({dur:.0f}s)")
    if dur < 5:
        q.fail(f"too short ({dur:.1f}s)")
    elif abs(dur - expected_s) > 1.5:
        q.warn(f"length {dur:.1f}s, expected {expected_s:.1f}s")

    # Black frames and silence in one pass over the file.
    proc = subprocess.run([ffmpeg_bin(), "-hide_banner", "-nostats", "-i", str(path),
                           "-vf", "blackdetect=d=0.5:pix_th=0.10", "-af", "silencedetect=n=-45dB:d=1.5",
                           "-f", "null", "-"], capture_output=True, text=True)
    black = sum(float(x) for x in re.findall(r"black_duration:([0-9.]+)", proc.stderr))
    black_start = [float(x) for x in re.findall(r"black_start:([0-9.]+)", proc.stderr)]
    silence = sum(float(x) for x in re.findall(r"silence_duration: ([0-9.]+)", proc.stderr))
    if dur:
        if black / dur > 0.3:
            q.fail(f"{black:.0f}s of black screen")
        elif black_start and black_start[0] < 0.6:
            q.warn("opens on a black frame")
        if silence / dur > 0.4:
            q.fail(f"{silence:.0f}s of silence")
        elif silence > 3:
            q.warn(f"{silence:.0f}s of silence")
    if copyright_risk > risk_limit:
        q.warn(f"copyright risk {copyright_risk:.0%}: likely music playing, check before posting")
    return q
