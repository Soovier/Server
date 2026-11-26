"""Import queue: streams you already have (uploaded files, a folder you drop
files into, or pasted links) get clipped right away, one after another.

    data/inbox/                 drop videos here
    data/inbox/<streamer>/      ...or in a folder named after the streamer
"""
from __future__ import annotations

import hashlib
import logging
import re
import threading
import time
from pathlib import Path

from .clipper import CLIP_LOCK, clip_source
from .config import settings
from .db import DB, get_db, utcnow
from .discovery import add_source

log = logging.getLogger("sxm.import")
VIDEO_EXT = {".mp4", ".mkv", ".mov", ".webm", ".flv", ".ts", ".m4v", ".avi"}
LAYOUTS = ("auto", "stack", "blur", "crop")


def inbox_dir() -> Path:
    d = settings.data_dir / "inbox"
    d.mkdir(parents=True, exist_ok=True)
    return d


def uploads_dir() -> Path:
    d = settings.media_dir / "sources" / "uploads"
    d.mkdir(parents=True, exist_ok=True)
    return d


def safe_name(name: str) -> str:
    stem, ext = Path(name).stem, Path(name).suffix.lower()
    stem = re.sub(r"[^A-Za-z0-9 _.-]+", "", stem).strip()[:80] or "video"
    return f"{stem}{ext if ext in VIDEO_EXT else '.mp4'}"


def _options(layout: str, clips: int | str | None, min_s=None, max_s=None) -> dict:
    try:
        n = max(1, min(int(clips or 0) or settings.clips_per_source, 15))
    except (TypeError, ValueError):
        n = settings.clips_per_source
    out = {"layout": layout if layout in LAYOUTS else None, "clips_wanted": n}
    out.update(lengths(min_s, max_s))
    return out


def lengths(min_s=None, max_s=None) -> dict:
    """Clean a shortest/longest pair from a form. Blank = use your default."""
    from .clipper import MAX_LEN
    try:
        lo = int(float(min_s)) if min_s not in (None, "", 0, "0") else None
        hi = int(float(max_s)) if max_s not in (None, "", 0, "0") else None
    except (TypeError, ValueError):
        return {}
    if lo is not None:
        lo = max(5, min(lo, MAX_LEN - 5))
    if hi is not None:
        hi = max((lo or 5) + 5, min(hi, MAX_LEN))
    return {k: v for k, v in (("min_s", lo), ("max_s", hi)) if v is not None}


def add_file(db: DB, path: Path, streamer: str = "", layout: str = "auto", clips: int | str | None = None,
             title: str = "", min_s=None, max_s=None) -> int | None:
    """Queue a video file that's already on this computer."""
    path = path.resolve()
    st = path.stat()
    key = hashlib.sha1(f"{path}|{st.st_size}".encode()).hexdigest()[:24]
    sid = add_source(db, platform="upload", external_id=key, url=path.as_uri(), streamer=streamer.strip(),
                     title=title or path.stem.replace("_", " "))
    if sid:
        db.update("sources", sid, local_path=str(path), status="downloaded", priority=10,
                  progress="Waiting in line", **_options(layout, clips, min_s, max_s))
        wake()
    return sid


def add_links(db: DB, text: str, streamer: str = "", layout: str = "auto", clips: int | str | None = None,
              min_s=None, max_s=None) -> list[int]:
    """Queue every link in a block of text (one per line, or separated by spaces)."""
    ids = []
    for url in re.findall(r"https?://\S+", text or ""):
        url = url.rstrip(").,]")
        ext = re.sub(r"[^a-zA-Z0-9]+", "_", url)[-120:]
        sid = add_source(db, platform="manual", external_id=ext, url=url, streamer=streamer.strip(), title=url)
        if sid:
            db.update("sources", sid, priority=10, progress="Waiting in line", **_options(layout, clips, min_s, max_s))
            ids.append(sid)
    if ids:
        wake()
    return ids


def scan_inbox(db: DB) -> int:
    """Queue new videos dropped into data/inbox (a subfolder name = the streamer)."""
    n = 0
    now = time.time()
    for f in sorted(inbox_dir().rglob("*")):
        if not f.is_file() or f.suffix.lower() not in VIDEO_EXT:
            continue
        if now - f.stat().st_mtime < 20:  # still being copied in
            continue
        rel = f.relative_to(inbox_dir())
        streamer = rel.parts[0] if len(rel.parts) > 1 else ""
        layout = db.get_setting("import_layout", "auto")
        clips = db.get_setting("import_clips", str(settings.clips_per_source))
        if add_file(db, f, streamer=streamer, layout=layout, clips=clips):
            n += 1
    return n


def retry(db: DB, source_id: int) -> None:
    db.update("sources", source_id, status="downloaded" if db.one(
        "SELECT local_path FROM sources WHERE id=? AND local_path IS NOT NULL", (source_id,)) else "new",
        error=None, progress="Waiting in line", priority=10)
    wake()


def remove(db: DB, source_id: int) -> None:
    db.update("sources", source_id, status="skipped", progress="Removed from the queue")


def cancel(db: DB, source_id: int) -> bool:
    """Cancel button: stop a download or clip job that is running, or drop one that is waiting."""
    from .clipper import cancel as stop_job
    src = db.one("SELECT status FROM sources WHERE id=?", (source_id,))
    if not src:
        return False
    if src["status"] in ("downloading", "clipping"):
        stop_job(source_id)
        db.update("sources", source_id, progress="Cancelling…", priority=0)
    elif src["status"] in ("new", "downloaded"):
        db.update("sources", source_id, status="skipped", priority=0, progress="Cancelled")
    else:
        return False
    return True


def queue(db: DB, limit: int = 60) -> list[dict]:
    rows = db.q("""SELECT s.*, (SELECT COUNT(*) FROM clips c WHERE c.source_id=s.id AND c.status='pending_review') to_review
                   FROM sources s WHERE s.priority>0 OR s.status IN ('downloading','clipping')
                   ORDER BY CASE s.status WHEN 'clipping' THEN 0 WHEN 'downloading' THEN 0
                                          WHEN 'downloaded' THEN 1 WHEN 'new' THEN 1 ELSE 2 END,
                            CASE WHEN s.status IN ('downloaded','new') THEN s.id END ASC, s.id DESC
                   LIMIT ?""", (limit,))
    pos = 0
    for r in rows:
        r["state"] = {"clipping": "working", "downloading": "working", "downloaded": "waiting", "new": "waiting",
                      "clipped": "done", "failed": "failed", "skipped": "removed"}.get(r["status"], r["status"])
        if r["state"] == "waiting":
            pos += 1
            r["position"] = pos
        r["name"] = r.get("title") or Path(r.get("local_path") or r["url"]).name
        mins = int((r.get("duration_s") or 0) // 60)
        r["length"] = f"{mins // 60}h {mins % 60}m" if mins >= 60 else (f"{mins}m" if mins else "")
    return rows


# ---------------- background worker ----------------

_wake = threading.Event()
_started = False


def wake() -> None:
    _wake.set()


def _loop() -> None:
    last_scan = 0.0
    while True:
        _wake.wait(timeout=30)
        _wake.clear()
        db = get_db()
        try:
            if time.time() - last_scan > 25:
                scan_inbox(db)
                last_scan = time.time()
            if db.get_setting("paused") == "1":
                continue
            while True:
                with CLIP_LOCK:
                    nxt = db.one("SELECT id FROM sources WHERE priority>0 AND status IN ('new','downloaded') "
                                 "ORDER BY priority DESC, id LIMIT 1")
                    if not nxt:
                        break
                    clip_source(db, nxt["id"])
                from .review import auto_approve_pending
                auto_approve_pending(db)
        except Exception:
            log.exception("import queue")


def start_worker() -> None:
    global _started
    if _started:
        return
    _started = True
    threading.Thread(target=_loop, name="sxm-import", daemon=True).start()
    wake()
