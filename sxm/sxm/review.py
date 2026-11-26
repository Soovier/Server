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
    """Streamers whose footage you have permission to post: the ones listed on the
    Sources page plus the ones you chose to watch in .env."""
    listed = {s.strip().lower() for s in db.get_setting("permitted_streamers", "").split(",") if s.strip()}
    return listed | {s.lower() for s in settings.twitch_streamers + settings.kick_streamers}


def has_permission(db: DB, clip: dict) -> bool:
    """A clip may be auto-posted only if its footage is from a campaign or a
    streamer you've marked as having given permission."""
    if clip.get("campaign_id"):
        return True
    src = db.one("SELECT streamer FROM sources WHERE id=?", (clip["source_id"],))
    return bool(src and (src["streamer"] or "").lower() in permitted_streamers(db))


def next_slots(db: DB, platform: str, count: int, now: datetime | None = None,
               channel_id: int | None = None) -> list[datetime]:
    """Next free posting slots for a channel (or a whole platform), in UTC."""
    tz = ZoneInfo(settings.timezone)
    now = (now or datetime.now(timezone.utc)).astimezone(tz)
    where, arg = ("channel_id=?", channel_id) if channel_id else ("platform=?", platform)
    taken = {r["scheduled_at"] for r in db.q(
        f"SELECT scheduled_at FROM posts WHERE {where} AND status IN ('scheduled','publishing','posted')",
        (arg,))}
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


def target_channels(db: DB) -> list[dict]:
    """Every channel SXM posts to: switched on, on a platform you post to."""
    plats = enabled_platforms(db)
    return [c for c in db.channels(enabled_only=True) if c["platform"] in plats and c.get("access_token")]


def schedule_clip(db: DB, clip_id: int, when: dict[str, datetime] | None = None) -> list[int]:
    """One post per channel you've switched on. If none are connected yet, the
    clip stays approved and is scheduled once you connect one."""
    ids = []
    for ch in target_channels(db):
        if db.one("SELECT id FROM posts WHERE clip_id=? AND channel_id=?", (clip_id, ch["id"])):
            continue
        at = (when or {}).get(ch["platform"]) or next_slots(db, ch["platform"], 1, channel_id=ch["id"])[0]
        ids.append(db.insert("posts", {"clip_id": clip_id, "platform": ch["platform"], "channel_id": ch["id"],
                                       "scheduled_at": at.astimezone(timezone.utc).replace(microsecond=0).isoformat()}))
    if db.one("SELECT id FROM posts WHERE clip_id=? AND status!='cancelled' LIMIT 1", (clip_id,)):
        db.update("clips", clip_id, status="scheduled")
    return ids


def schedule_waiting(db: DB) -> str:
    """Approved clips that had nowhere to go: schedule them now if a channel is on."""
    if not target_channels(db):
        return "no channels switched on"
    n = 0
    for c in db.q("SELECT id FROM clips WHERE status='approved'"):
        n += bool(schedule_clip(db, c["id"]))
    return f"scheduled {n} waiting clips"


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
    max_risk = float(db.get_setting("auto_approve_max_copyright_risk", "0.5") or 0.5)
    for c in db.q("SELECT * FROM clips WHERE status='pending_review' ORDER BY score DESC"):
        # Clips that failed quality control or look risky for copyright wait for you.
        if (c["score"] or 0) < min_score or not has_permission(db, c) or c.get("qc_status") == "fail" \
                or (c.get("copyright_risk") or 0) > max_risk:
            skipped += 1
            continue
        approve(db, c["id"], note="auto-approved")
        n += 1
    return f"auto-approved {n}, left {skipped} for manual review"


def paste_text(clip: dict) -> dict:
    """Ready-to-paste copy for each platform: one caption with hashtags, plus a
    YouTube title (max 100 chars, with #shorts)."""
    caption = (clip.get("caption") or "").strip()
    tags = " ".join(dict.fromkeys((clip.get("hashtags") or "").split()))
    full = f"{caption}\n\n{tags}".strip() if tags else caption
    title = (clip.get("hook") or caption or "Clip").strip()
    if "#shorts" not in title.lower():
        title = f"{title[:91].rstrip()} #shorts"
    return {"caption": full[:2200], "title": title[:100], "tags": tags}


def download_name(clip: dict) -> str:
    import re
    slug = re.sub(r"[^a-z0-9]+", "-", (clip.get("hook") or "clip").lower()).strip("-")[:40] or "clip"
    return f"sxm-{clip['id']}-{slug}.mp4"
