"""Content Rewards campaigns: tracking, assisted submit, and earnings.

Content Rewards has no public API, and its terms say "We do not post on your
behalf" and that a post must be submitted "within 30 minutes of publishing
it". So submission here is assisted, not automated: once a campaign clip goes
live, Krypt Marketo alerts you, and the Submit button copies the post link and
opens the campaign page so it takes one paste + click. Automating the
logged-in Content Rewards site with a bot could trip its fraud checks (the
"Bot Score" in their terms) and get the account banned, which isn't worth it.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from .db import DB, utcnow
from .notify import notify

SUBMIT_WINDOW_MIN = 30


def add_campaign(db: DB, *, name: str, url: str, source_urls: str = "", rate_per_1k: float = 0,
                 max_payout: float = 0, platforms: str = "instagram,youtube", rules: str = "",
                 required_tags: str = "") -> int:
    return db.insert("campaigns", {
        "name": name, "url": url, "source_urls": source_urls, "rate_per_1k": rate_per_1k,
        "max_payout": max_payout, "platforms": platforms, "rules": rules, "required_tags": required_tags,
        "created_at": utcnow(),
    })


def queue_submission(db: DB, campaign_id: int, post_id: int) -> int | None:
    camp = db.one("SELECT * FROM campaigns WHERE id=?", (campaign_id,))
    post = db.one("SELECT * FROM posts WHERE id=?", (post_id,))
    if not camp or not post:
        return None
    if post["platform"] not in (camp["platforms"] or "").split(","):
        return None
    sid = db.insert("submissions", {"campaign_id": campaign_id, "post_id": post_id}, ignore=True)
    notify(f"Submit to '{camp['name']}' within {SUBMIT_WINDOW_MIN} min: {post['permalink']}",
           link=camp["url"])
    return sid


def pending_submissions(db: DB) -> list[dict]:
    rows = db.q("""
        SELECT s.*, c.name campaign, c.url campaign_url, p.permalink, p.platform, p.posted_at
        FROM submissions s JOIN campaigns c ON c.id=s.campaign_id JOIN posts p ON p.id=s.post_id
        WHERE s.status='ready' ORDER BY p.posted_at""")
    now = datetime.now(timezone.utc)
    for r in rows:
        posted = datetime.fromisoformat(r["posted_at"]) if r["posted_at"] else now
        left = posted + timedelta(minutes=SUBMIT_WINDOW_MIN) - now
        r["minutes_left"] = max(int(left.total_seconds() // 60), 0)
        r["late"] = left.total_seconds() < 0
    return rows


def mark_submitted(db: DB, submission_id: int) -> None:
    db.update("submissions", submission_id, status="submitted", submitted_at=utcnow())


def set_status(db: DB, submission_id: int, status: str, payout_usd: float | None = None) -> None:
    fields = {"status": status}
    if payout_usd is not None:
        fields["payout_usd"] = payout_usd
    db.update("submissions", submission_id, **fields)


def estimated_earnings(db: DB) -> list[dict]:
    """Per campaign: views on submitted posts x rate, capped at max payout."""
    rows = db.q("""
        SELECT c.id, c.name, c.rate_per_1k, c.max_payout,
               COUNT(s.id) submissions,
               SUM(CASE WHEN s.status IN ('submitted','approved','paid') THEN 1 ELSE 0 END) live,
               COALESCE(SUM(CASE WHEN s.status IN ('submitted','approved','paid') THEN
                   (SELECT views FROM metrics m WHERE m.post_id=s.post_id ORDER BY m.id DESC LIMIT 1) END),0) views,
               COALESCE(SUM(CASE WHEN s.status='paid' THEN s.payout_usd END),0) paid
        FROM campaigns c LEFT JOIN submissions s ON s.campaign_id=c.id
        GROUP BY c.id ORDER BY c.id DESC""")
    for r in rows:
        est = (r["views"] or 0) / 1000 * (r["rate_per_1k"] or 0)
        if r["max_payout"]:
            est = min(est, r["max_payout"])
        r["estimated"] = round(est, 2)
    return rows
