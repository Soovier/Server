"""Clipper submissions: submit, review, view refresh."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from . import ledger, verify
from .config import settings
from .db import DB, utcnow


class SubmitError(ValueError):
    pass


def submit(db: DB, clipper_id: int, campaign_id: int, post_url: str, reported_views: int = 0,
           proof_path: str | None = None, posted_at: str | None = None, http=None) -> int:
    c = db.one("SELECT * FROM campaigns WHERE id=?", (campaign_id,))
    if not c or c["status"] != "active":
        raise SubmitError("This campaign isn't accepting submissions")
    if c["ends_at"] and c["ends_at"] < utcnow():
        raise SubmitError("This campaign has ended")
    post_url = post_url.strip()
    platform = verify.platform_of(post_url)
    if not platform:
        raise SubmitError("Paste a YouTube, Instagram, Facebook or TikTok post link")
    if platform not in c["platforms"].split(","):
        raise SubmitError(f"This campaign doesn't pay for {platform} posts")
    if db.one("SELECT id FROM submissions WHERE post_url=?", (post_url,)):
        raise SubmitError("That post was already submitted")
    try:
        ext = verify.post_id(platform, post_url)
    except verify.VerifyError as e:
        raise SubmitError(str(e))
    if db.one("SELECT id FROM submissions WHERE platform=? AND external_id=?", (platform, ext)):
        raise SubmitError("That post was already submitted")
    if not db.one("SELECT id FROM social_accounts WHERE user_id=? AND platform=?", (clipper_id, platform)):
        raise SubmitError(f"Add your {platform} account on your profile first")

    row = {"campaign_id": campaign_id, "clipper_id": clipper_id, "platform": platform, "post_url": post_url,
           "external_id": ext, "reported_views": max(int(reported_views or 0), 0), "proof_path": proof_path,
           "posted_at": posted_at, "submitted_at": utcnow()}
    sid = db.insert("submissions", row)
    # YouTube: check right away that the video exists, is public and is on their channel.
    if platform == "youtube" and settings.youtube_api_key:
        s = db.one("SELECT * FROM submissions WHERE id=?", (sid,))
        try:
            views = verify.check_views(db, s, http)
            db.update("submissions", sid, views=views, views_source="api", views_checked_at=utcnow())
        except verify.VerifyError as e:
            db.run("DELETE FROM submissions WHERE id=?", (sid,))
            raise SubmitError(str(e))
    elif reported_views:
        db.update("submissions", sid, views_source="reported")
    return sid


def can_review(db: DB, user: dict, submission_id: int) -> dict:
    s = db.one("""SELECT s.*, c.owner_id FROM submissions s JOIN campaigns c ON c.id=s.campaign_id
                  WHERE s.id=?""", (submission_id,))
    if not s or (user["role"] != "admin" and s["owner_id"] != user["id"]):
        raise SubmitError("Not your campaign")
    return s


def approve(db: DB, user: dict, submission_id: int, confirmed_views: int | None = None, note: str = "") -> None:
    s = can_review(db, user, submission_id)
    fields = {"status": "approved", "reviewed_at": utcnow(), "note": note or None}
    if s["views_source"] != "api":
        # Brand confirms the reported number (from the screenshot) before it pays.
        fields["views"] = max(int(confirmed_views if confirmed_views is not None else s["reported_views"] or 0), 0)
    db.update("submissions", submission_id, **fields)
    ledger.recalc_submission(db, submission_id)


def reject(db: DB, user: dict, submission_id: int, note: str = "") -> None:
    s = can_review(db, user, submission_id)
    if s["status"] == "approved" and s["earned_cents"]:
        raise SubmitError("This post has already earned money; contact the admin to reverse it")
    db.update("submissions", submission_id, status="rejected", reviewed_at=utcnow(), note=note or None)


def report_views(db: DB, clipper_id: int, submission_id: int, views: int, proof_path: str | None = None) -> None:
    s = db.one("SELECT * FROM submissions WHERE id=? AND clipper_id=?", (submission_id, clipper_id))
    if not s:
        raise SubmitError("Not your submission")
    if s["views_source"] == "api":
        raise SubmitError("Views for this post are checked automatically")
    fields = {"reported_views": max(int(views), 0), "views_source": "reported"}
    if proof_path:
        fields["proof_path"] = proof_path
    db.update("submissions", submission_id, **fields)


def confirm_views(db: DB, user: dict, submission_id: int, views: int) -> None:
    s = can_review(db, user, submission_id)
    if views < (s["views"] or 0):
        raise SubmitError("Views can only go up")
    db.update("submissions", submission_id, views=views)
    ledger.recalc_submission(db, submission_id)


def refresh_views(db: DB, http=None, max_age_days: int = 30) -> str:
    """Re-check API views for approved/pending posts younger than max_age_days."""
    since = (datetime.now(timezone.utc) - timedelta(days=max_age_days)).isoformat()
    rows = db.q("""SELECT * FROM submissions WHERE status IN ('pending','approved') AND views_source='api'
                   AND submitted_at>=?""", (since,))
    n = added = 0
    for s in rows:
        try:
            views = verify.check_views(db, s, http)
        except verify.VerifyError as e:
            db.update("submissions", s["id"], note=f"view check: {e}"[:200])
            continue
        if views is None:
            continue
        db.update("submissions", s["id"], views=max(views, s["views"] or 0), views_checked_at=utcnow())
        added += ledger.recalc_submission(db, s["id"])
        n += 1
    return f"checked {n} posts, +${added / 100:.2f} earned"
