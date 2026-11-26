"""Review queue, auto-approve, and post scheduling."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from .config import settings
from .db import DB, utcnow

PLATFORMS = ("instagram", "facebook", "youtube")


def enabled_platforms(db: DB) -> list[str]:
    raw = db.get_setting("post_platforms", ",".join(PLATFORMS))
    return [p for p in raw.split(",") if p in PLATFORMS]


def permitted_streamers(db: DB) -> set[str]:
    return {s.strip().lower() for s in db.get_setting("permitted_streamers", "").split(",") if s.strip()}


def has_permission(db: DB, clip: dict) -> bool:
    """A clip may be auto-posted only if its footage is from a campaign or a
    streamer you've marked as having given permission."""
    if clip.get("campaign_id"):
        return True
    src = db.one("SELECT streamer FROM sources WHERE id=?", (clip["source_id"],))
    return bool(src and (src["streamer"] or "").lower() in permitted_streamers(db))


def next_slots(db: DB, platform: str, count: int, now: datetime | None = None) -> list[datetime]:
    """Next free posting slots for a platform, in UTC."""
    tz = ZoneInfo(settings.timezone)
    now = (now or datetime.now(timezone.utc)).astimezone(tz)
    taken = {r["scheduled_at"] for r in db.q(
        "SELECT scheduled_at FROM posts WHERE platform=? AND status IN ('scheduled','publishing','posted')",
        (platform,))}
    out: list[datetime] = []
    day = now.date()
    while len(out) < count:
        for slot in sorted(settings.post_slots):
            hh, mm = (int(x) for x in slot.split(":"))
            local = datetime(day.year, day.month, day.day, hh, mm, tzinfo=tz)
            if local <= now + timedelta(minutes=5):
                continue
            utc = local.astimezone(timezone.utc).replace(microsecond=0)
            if utc.isoformat() in taken:
                continue
            out.append(utc)
            taken.add(utc.isoformat())
            if len(out) >= count:
                break
        day += timedelta(days=1)
    return out


def schedule_clip(db: DB, clip_id: int, when: dict[str, datetime] | None = None) -> list[int]:
    ids = []
    for p in enabled_platforms(db):
        if db.one("SELECT id FROM posts WHERE clip_id=? AND platform=?", (clip_id, p)):
            continue
        at = (when or {}).get(p) or next_slots(db, p, 1)[0]
        ids.append(db.insert("posts", {"clip_id": clip_id, "platform": p,
                                       "scheduled_at": at.astimezone(timezone.utc).replace(microsecond=0).isoformat()}))
    db.update("clips", clip_id, status="scheduled")
    return ids


def approve(db: DB, clip_id: int, note: str = "") -> list[int]:
    db.update("clips", clip_id, status="approved", reviewed_at=utcnow(), review_note=note or None)
    return schedule_clip(db, clip_id)


def reject(db: DB, clip_id: int, note: str = "") -> None:
    db.update("clips", clip_id, status="rejected", reviewed_at=utcnow(), review_note=note or None)
    db.run("UPDATE posts SET status='cancelled' WHERE clip_id=? AND status='scheduled'", (clip_id,))


def edit_copy(db: DB, clip_id: int, *, hook: str | None = None, caption: str | None = None,
              hashtags: str | None = None) -> None:
    fields = {k: v for k, v in {"hook": hook, "caption": caption, "hashtags": hashtags}.items() if v is not None}
    db.update("clips", clip_id, **fields)


def auto_approve_pending(db: DB) -> str:
    if db.get_setting("auto_approve") != "1":
        return "auto-approve off"
    min_score = float(db.get_setting("auto_approve_min_score", "0") or 0)
    n = skipped = 0
    for c in db.q("SELECT * FROM clips WHERE status='pending_review' ORDER BY score DESC"):
        if (c["score"] or 0) < min_score or not has_permission(db, c):
            skipped += 1
            continue
        approve(db, c["id"], note="auto-approved")
        n += 1
    return f"auto-approved {n}, left {skipped} for manual review"
