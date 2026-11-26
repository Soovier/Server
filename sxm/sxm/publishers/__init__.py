"""Publishes scheduled posts when they're due."""
from __future__ import annotations

from datetime import datetime, timezone

from ..db import DB, utcnow
from ..notify import notify
from .meta import publish_facebook, publish_instagram
from .youtube import publish_youtube

PUBLISHERS = {
    "instagram": publish_instagram,
    "facebook": publish_facebook,
    "youtube": publish_youtube,
}
MAX_ATTEMPTS = 3


def publish_post(db: DB, post_id: int) -> dict:
    post = db.one("SELECT * FROM posts WHERE id=?", (post_id,))
    clip = db.one("SELECT * FROM clips WHERE id=?", (post["clip_id"],))
    db.update("posts", post_id, status="publishing", attempts=(post["attempts"] or 0) + 1)
    try:
        channel = db.get_channel(post["channel_id"]) if post.get("channel_id") else None
        res = PUBLISHERS[post["platform"]](db, clip, channel)
    except Exception as e:
        failed = (post["attempts"] or 0) + 1 >= MAX_ATTEMPTS
        db.update("posts", post_id, status="failed" if failed else "scheduled", error=str(e)[:800])
        if failed:
            where = (db.get_channel(post["channel_id"]) or {}).get("name") if post.get("channel_id") else None
            notify(f"Post failed on {post['platform']}{f' ({where})' if where else ''} for clip #{clip['id']}: {e}")
        raise
    db.update("posts", post_id, status="posted", external_id=res["external_id"], permalink=res.get("permalink"),
              posted_at=utcnow(), error=None)
    remaining = db.one("SELECT COUNT(*) n FROM posts WHERE clip_id=? AND status IN ('scheduled','publishing')",
                       (clip["id"],))["n"]
    if not remaining:
        db.update("clips", clip["id"], status="posted")
    # Content Rewards wants the link within 30 minutes of posting.
    if clip.get("campaign_id"):
        from ..campaigns import queue_submission
        queue_submission(db, clip["campaign_id"], post_id)
    return res


def publish_due(db: DB, now: datetime | None = None) -> str:
    if db.get_setting("paused") == "1":
        return "paused"
    now_s = (now or datetime.now(timezone.utc)).replace(microsecond=0).isoformat()
    due = db.q("SELECT id FROM posts WHERE status='scheduled' AND scheduled_at<=? ORDER BY scheduled_at", (now_s,))
    ok = bad = 0
    for p in due:
        try:
            publish_post(db, p["id"])
            ok += 1
        except Exception:
            bad += 1
    return f"published {ok}, failed {bad}"
