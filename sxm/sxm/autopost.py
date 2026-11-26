"""Auto-post: pick an account and SXM posts one clip to it every few hours,
for as long as there are clips to post.

Where the clips come from (per account):
    library              your ready-made clips plus every clip SXM approved
    trending:<row>       SXM clips the top videos of a Trending carousel for you,
                         then posts them once they're approved (falls back to library)
"""
from __future__ import annotations

import hashlib
import shutil
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .config import settings
from .db import DB, utcnow

SOURCES = [
    ("library", "📦 Your ready clips", "Clips you dropped in plus every clip SXM made and approved."),
    ("trending:streamers", "🔥 AI clips: Streamer videos blowing up", "SXM clips the hottest streamer videos for you."),
    ("trending:gta", "🚗 AI clips: GTA right now", "SXM clips the GTA videos taking off this week."),
    ("trending:games", "🎮 AI clips: Other games going viral", "SXM clips what's charting in Gaming."),
]
SOURCE_KEYS = {k for k, _, _ in SOURCES}
READY = ("approved", "library", "scheduled", "posted")
LOW_STOCK = 2  # clip more trending videos when an account has fewer than this many ready


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).replace(microsecond=0).isoformat()


def _parse(ts: str | None) -> datetime | None:
    try:
        return datetime.fromisoformat(ts) if ts else None
    except ValueError:
        return None


# ---------------- rules ----------------

def rules(db: DB) -> list[dict]:
    return db.q("""SELECT a.*, c.name channel_name, c.platform, c.avatar FROM autopost a
                   JOIN channels c ON c.id=a.channel_id ORDER BY a.id""")


def save_rule(db: DB, channel_id: int, every_hours: float = 4, source: str = "library", enabled: bool = True) -> int:
    if not db.get_channel(channel_id):
        raise ValueError("Pick an account to post to")
    every_hours = max(1.0, min(float(every_hours or 4), 48.0))
    source = source if source in SOURCE_KEYS else "library"
    have = db.one("SELECT id FROM autopost WHERE channel_id=?", (channel_id,))
    if have:
        db.update("autopost", have["id"], every_hours=every_hours, source=source, enabled=int(enabled))
        rid = have["id"]
    else:
        rid = db.insert("autopost", {"channel_id": channel_id, "every_hours": every_hours, "source": source,
                                     "enabled": int(enabled), "created_at": utcnow()})
    # Normal-slot posts already waiting for this account move onto the auto-post timer.
    if enabled:
        db.run("UPDATE posts SET status='cancelled' WHERE channel_id=? AND status='scheduled' AND auto=0", (channel_id,))
    run(db)
    return rid


def set_enabled(db: DB, rule_id: int, enabled: bool) -> None:
    r = db.one("SELECT * FROM autopost WHERE id=?", (rule_id,))
    if not r:
        return
    db.update("autopost", rule_id, enabled=int(enabled))
    if not enabled:
        db.run("UPDATE posts SET status='cancelled' WHERE channel_id=? AND status='scheduled' AND auto=1",
               (r["channel_id"],))
        from .review import schedule_waiting
        schedule_waiting(db)
    else:
        run(db)


def remove_rule(db: DB, rule_id: int) -> None:
    set_enabled(db, rule_id, False)
    db.run("UPDATE sources SET autopost_id=NULL WHERE autopost_id=?", (rule_id,))
    db.run("DELETE FROM autopost WHERE id=?", (rule_id,))


# ---------------- picking clips ----------------

def ready_clips(db: DB, rule: dict) -> list[dict]:
    """Clips this account hasn't had yet, best first. A trending rule uses its own clips first."""
    rows = db.q(f"""SELECT c.*, s.autopost_id FROM clips c JOIN sources s ON s.id=c.source_id
                    WHERE c.status IN ({','.join('?' * len(READY))}) AND c.video_path IS NOT NULL
                      AND NOT EXISTS (SELECT 1 FROM posts p WHERE p.clip_id=c.id AND p.channel_id=?
                                      AND p.status!='cancelled')""", (*READY, rule["channel_id"]))
    rows = [r for r in rows if Path(r["video_path"]).exists()]
    mine = rule["source"] != "library"
    return sorted(rows, key=lambda c: (not (mine and c["autopost_id"] == rule["id"]), -(c["score"] or 0), -c["id"]))


def next_time(db: DB, rule: dict, now: datetime | None = None) -> datetime:
    now = now or datetime.now(timezone.utc)
    last = db.one("""SELECT MAX(COALESCE(posted_at, scheduled_at)) t FROM posts
                     WHERE channel_id=? AND auto=1 AND status IN ('scheduled','publishing','posted')""",
                  (rule["channel_id"],))
    t = _parse(last and last["t"])
    nxt = t + timedelta(hours=rule["every_hours"]) if t else now + timedelta(minutes=2)
    return max(nxt, now + timedelta(minutes=2))


def feed_from_trending(db: DB, rule: dict) -> int:
    """Send the next video from the chosen carousel to the clipper, so the account doesn't run dry."""
    from . import trending
    row = rule["source"].split(":", 1)[1]
    data = trending.cached(db) or {}
    cards = [c for tab in ("now", "all") for c in data.get("rows", {}).get(row, {}).get(tab, [])]
    in_flight = db.one("""SELECT COUNT(*) n FROM sources WHERE autopost_id=? AND status IN
                          ('new','downloading','downloaded','clipping')""", (rule["id"],))["n"]
    if in_flight:
        return 0
    for c in cards:
        sid = trending.queue_clip(db, c["url"], c.get("title", ""), c.get("channel", ""))
        if sid:
            db.update("sources", sid, autopost_id=rule["id"])
            return 1
    return 0


def run(db: DB, now: datetime | None = None) -> str:
    """Line up the next post for each auto-post account. Runs every few minutes."""
    now = now or datetime.now(timezone.utc)
    notes = []
    for rule in db.q("SELECT * FROM autopost WHERE enabled=1"):
        ch = db.get_channel(rule["channel_id"])
        if not ch:
            continue
        clips = ready_clips(db, rule)
        if rule["source"].startswith("trending:") and len(clips) < LOW_STOCK:
            notes.append(f"{ch['name']}: clipping {feed_from_trending(db, rule)} trending video")
        waiting = db.one("SELECT id FROM posts WHERE channel_id=? AND auto=1 AND status IN ('scheduled','publishing')",
                         (rule["channel_id"],))
        if waiting or not clips:
            continue
        clip = clips[0]
        at = next_time(db, rule, now)
        db.insert("posts", {"clip_id": clip["id"], "platform": ch["platform"], "channel_id": ch["id"],
                            "scheduled_at": _iso(at), "auto": 1})
        if clip["status"] in ("approved", "library"):
            db.update("clips", clip["id"], status="scheduled")
        notes.append(f"{ch['name']}: clip #{clip['id']} at {_iso(at)}")
    return "; ".join(notes) or "nothing to line up"


def summary(db: DB) -> list[dict]:
    """Each rule with how many clips are ready and how long they'll last."""
    from .home import _in  # "in 3h" style text
    out = []
    for r in rules(db):
        n = len(ready_clips(db, r))
        nxt = db.one("""SELECT p.scheduled_at, c.hook, c.id clip_id FROM posts p JOIN clips c ON c.id=p.clip_id
                        WHERE p.channel_id=? AND p.auto=1 AND p.status='scheduled' ORDER BY p.scheduled_at LIMIT 1""",
                     (r["channel_id"],))
        hours = n * r["every_hours"] + (r["every_hours"] if nxt else 0)
        r["ready"] = n
        r["lasts"] = (f"{hours / 24:.1f} days" if hours >= 48 else f"{hours:.0f} hours") if hours else "nothing lined up"
        r["next"] = nxt
        r["next_in"] = _in(nxt["scheduled_at"]) if nxt else ""
        r["source_label"] = next((l for k, l, _ in SOURCES if k == r["source"]), r["source"])
        r["making"] = db.one("""SELECT COUNT(*) n FROM sources WHERE autopost_id=? AND status IN
                                ('new','downloading','downloaded','clipping')""", (r["id"],))["n"]
        out.append(r)
    return out


# ---------------- ready-made clips ----------------

def premade_dir() -> Path:
    d = settings.media_dir / "clips"
    d.mkdir(parents=True, exist_ok=True)
    return d


def add_premade(db: DB, path: Path, title: str = "", caption: str = "", hashtags: str = "") -> int:
    """A finished clip you made yourself: it skips editing and goes straight into the library."""
    from .editor import probe_duration
    from .discovery import add_source
    st = path.stat()
    key = hashlib.sha1(f"{path.name}|{st.st_size}|{st.st_mtime}".encode()).hexdigest()[:24]
    title = (title or path.stem.replace("_", " ").replace("-", " ")).strip()[:100]
    sid = add_source(db, platform="premade", external_id=key, url=path.as_uri(), title=title) or \
        db.one("SELECT id FROM sources WHERE platform='premade' AND external_id=?", (key,))["id"]
    dur = probe_duration(path) or 0
    cid = db.insert("clips", {"source_id": sid, "start_s": 0, "end_s": dur, "score": 5, "hook": title,
                              "caption": caption or title, "hashtags": hashtags or "#shorts #gaming #clips",
                              "status": "library", "qc_status": "pass", "created_at": utcnow()})
    dest = premade_dir() / f"clip_{cid}.mp4"
    shutil.move(str(path), dest)
    thumb = premade_dir() / f"clip_{cid}.jpg"
    try:
        from .editor import ffmpeg_bin
        subprocess.run([ffmpeg_bin(), "-v", "error", "-y", "-ss", str(min(1.0, dur / 2)), "-i", str(dest),
                        "-frames:v", "1", "-vf", "scale=540:-2", str(thumb)], capture_output=True, timeout=60)
    except Exception:
        pass
    db.update("clips", cid, video_path=str(dest), thumb_path=str(thumb) if thumb.exists() else None)
    db.update("sources", sid, status="clipped", clips_made=1)
    run(db)
    return cid
