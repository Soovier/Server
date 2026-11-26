"""SXM Inventory: every video at every stage, on one board."""
from __future__ import annotations

import re
from pathlib import Path

from .db import DB
from .home import _ago, _in, _when

STATUS_LABEL = {"pending_review": "Waiting for review", "approved": "Approved", "library": "Ready-made",
                "scheduled": "Lined up", "posted": "Posted", "rejected": "Skipped", "failed": "Failed",
                "rendering": "Rendering"}


def _name(r: dict) -> str:
    return r.get("title") or Path(r.get("local_path") or r.get("url") or "").name or f"Video #{r['id']}"


def board(db: DB) -> dict:
    queued = db.q("""SELECT * FROM sources WHERE status IN ('new','downloading','downloaded')
                     AND (priority>0 OR status='downloading' OR autopost_id IS NOT NULL OR found_at > datetime('now','-2 days'))
                     ORDER BY CASE status WHEN 'downloading' THEN 0 ELSE 1 END, priority DESC, id LIMIT 40""")
    for i, r in enumerate(queued):
        r["name"] = _name(r)
        m = re.search(r"(\d+)%", r.get("progress") or "")
        r["pct"] = int(m.group(1)) if m and r["status"] == "downloading" else (100 if r["status"] == "downloaded" else 0)
        if r["status"] == "downloading":
            r["line"] = r.get("progress") or "Downloading"
        elif r["status"] == "downloaded":
            r["line"] = "Downloaded · waiting to be clipped"
        else:
            r["line"] = f"#{i + 1} in line to download"
    processing = db.q("SELECT * FROM sources WHERE status='clipping' ORDER BY id")
    for r in processing:
        r["name"] = _name(r)
        r["line"] = r.get("progress") or "Clipping"
    processed = db.q("""SELECT c.id, c.hook, c.status, c.score, c.created_at, c.thumb_path, c.end_s - c.start_s secs,
                               s.streamer, s.platform src_platform
                        FROM clips c JOIN sources s ON s.id=c.source_id
                        WHERE c.status NOT IN ('rendering','rejected') ORDER BY c.id DESC LIMIT 30""")
    for r in processed:
        r["label"] = STATUS_LABEL.get(r["status"], r["status"])
        r["ago"] = _ago(r["created_at"])
    post_sql = """SELECT p.*, c.hook, c.thumb_path, ch.name channel_name FROM posts p JOIN clips c ON c.id=p.clip_id
                  LEFT JOIN channels ch ON ch.id=p.channel_id WHERE {w} ORDER BY {o} LIMIT 30"""
    upcoming = db.q(post_sql.format(w="p.status IN ('scheduled','publishing') AND p.auto=0", o="p.scheduled_at"))
    auto = db.q(post_sql.format(w="p.status IN ('scheduled','publishing') AND p.auto=1", o="p.scheduled_at"))
    for r in upcoming + auto:
        r["when"], r["in"] = _when(r["scheduled_at"]), _in(r["scheduled_at"])
    posted = db.q(post_sql.format(w="p.status IN ('posted','failed')", o="COALESCE(p.posted_at, p.scheduled_at) DESC"))
    for r in posted:
        r["ago"] = _ago(r.get("posted_at") or r["scheduled_at"])
    cols = [
        {"key": "queued", "title": "Queued", "icon": "⏳", "items": queued},
        {"key": "processing", "title": "Processing", "icon": "⚙️", "items": processing},
        {"key": "processed", "title": "Processed", "icon": "🎬", "items": processed},
        {"key": "upcoming", "title": "About to post", "icon": "📅", "items": upcoming},
        {"key": "auto", "title": "About to auto-post", "icon": "🤖", "items": auto},
        {"key": "posted", "title": "Just posted", "icon": "✅", "items": posted},
    ]
    return {"cols": cols, "counts": {c["key"]: len(c["items"]) for c in cols}}
