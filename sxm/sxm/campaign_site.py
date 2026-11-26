"""Connects SXM to your SXM Campaigns site: pulls live campaigns in,
submits posts the moment they go live, and sends Instagram/Facebook view counts
(which only your own account can see) so they can be confirmed and paid."""
from __future__ import annotations

import httpx

from .config import settings
from .db import DB, utcnow


def enabled() -> bool:
    return bool(settings.campaigns_site_url and settings.campaigns_site_token)


def _client(http: httpx.Client | None = None) -> httpx.Client:
    return http or httpx.Client(timeout=30)


def _h() -> dict:
    return {"Authorization": f"Bearer {settings.campaigns_site_token}"}


def sync_campaigns(db: DB, http: httpx.Client | None = None) -> int:
    """Mirror active SXM Campaigns campaigns into the local campaigns table."""
    r = _client(http).get(f"{settings.campaigns_site_url}/api/v1/campaigns", headers=_h())
    r.raise_for_status()
    seen = set()
    n = 0
    for c in r.json():
        seen.add(c["id"])
        row = {
            "name": c["title"], "url": c["url"], "source_urls": c.get("source_urls") or "",
            "rate_per_1k": c["rate_cents_per_1k"] / 100, "max_payout": (c.get("max_cents_per_post") or 0) / 100,
            "platforms": c["platforms"], "rules": c.get("rules") or "", "required_tags": c.get("required_tags") or "",
            "active": 1,
        }
        local = db.one("SELECT id FROM campaigns WHERE site_campaign_id=?", (c["id"],))
        if local:
            db.update("campaigns", local["id"], **row)
        else:
            db.insert("campaigns", {**row, "site_campaign_id": c["id"], "created_at": utcnow()})
            n += 1
    for local in db.q("SELECT id, site_campaign_id FROM campaigns WHERE site_campaign_id IS NOT NULL"):
        if local["site_campaign_id"] not in seen:
            db.update("campaigns", local["id"], active=0)
    return n


def submit(db: DB, submission_id: int, http: httpx.Client | None = None) -> bool:
    """Submit one queued submission. Returns True on success."""
    s = db.one("""SELECT s.*, c.site_campaign_id, p.permalink, p.posted_at FROM submissions s
                  JOIN campaigns c ON c.id=s.campaign_id JOIN posts p ON p.id=s.post_id WHERE s.id=?""",
               (submission_id,))
    if not s or not s["site_campaign_id"] or not s["permalink"]:
        return False
    r = _client(http).post(f"{settings.campaigns_site_url}/api/v1/submissions", headers=_h(), json={
        "campaign_id": s["site_campaign_id"], "post_url": s["permalink"], "posted_at": s["posted_at"]})
    if r.status_code == 200:
        db.update("submissions", submission_id, status="submitted", submitted_at=utcnow(),
                  site_submission_id=r.json()["id"], note=None)
        return True
    try:
        err = r.json().get("error", r.text)
    except ValueError:
        err = r.text
    db.update("submissions", submission_id, note=f"auto-submit failed: {err}"[:300])
    return False


def push_views(db: DB, http: httpx.Client | None = None) -> int:
    """Send latest Instagram/Facebook views for submitted posts (YouTube is checked by the site itself)."""
    rows = db.q("""SELECT s.site_submission_id rid,
                          (SELECT views FROM metrics m WHERE m.post_id=s.post_id ORDER BY m.id DESC LIMIT 1) views
                   FROM submissions s JOIN posts p ON p.id=s.post_id
                   WHERE s.site_submission_id IS NOT NULL AND p.platform IN ('instagram','facebook')""")
    http = _client(http)
    n = 0
    for r in rows:
        if r["views"]:
            resp = http.post(f"{settings.campaigns_site_url}/api/v1/submissions/{r['rid']}/views", headers=_h(),
                             json={"views": int(r["views"])})
            n += resp.status_code == 200
    return n
