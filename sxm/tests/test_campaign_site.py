import hashlib
import hmac
import json
import re
import time

import httpx
import pytest
from fastapi.testclient import TestClient

from sxm_campaigns import auth, ledger, stripe_api, submissions, verify
from sxm_campaigns.config import settings as rs
from sxm_campaigns.db import DB, set_db
from sxm_campaigns.web import app


@pytest.fixture
def db(tmp_path, monkeypatch):
    monkeypatch.setattr(rs, "data_dir", tmp_path)
    monkeypatch.setattr(rs, "admin_email", "boss@sxm.test")
    monkeypatch.setattr(rs, "platform_fee_percent", 10.0)
    monkeypatch.setattr(rs, "youtube_api_key", "YTKEY")
    monkeypatch.setattr(rs, "stripe_secret_key", "")
    d = DB(":memory:")
    set_db(d)
    return d


def yt_transport(channel_desc="hello SXM-CODE", views=25000, channel_id="UCme"):
    def handler(req: httpx.Request):
        if req.url.path.endswith("/channels"):
            return httpx.Response(200, json={"items": [{"id": channel_id, "snippet": {"description": channel_desc}}]})
        if req.url.path.endswith("/videos"):
            return httpx.Response(200, json={"items": [{"id": req.url.params["id"],
                                                        "snippet": {"channelId": channel_id},
                                                        "statistics": {"viewCount": str(views)}}]})
        return httpx.Response(404)
    return httpx.MockTransport(handler)


_REAL_CLIENT = httpx.Client


def patch_http(monkeypatch, transport):
    monkeypatch.setattr(verify.httpx, "Client", lambda **kw: _REAL_CLIENT(transport=transport))


def client_for(email, role, name=None):
    c = TestClient(app)
    r = c.post("/signup", data={"email": email, "name": name or email.split("@")[0], "password": "password123",
                                "role": role}, follow_redirects=False)
    assert r.status_code == 303, r.text
    return c


def csrf_of(c, path="/profile"):
    return re.search(r'name="csrf" value="([^"]+)"', c.get(path).text).group(1)


# ---------------- unit ----------------

def test_password_and_session_roundtrip():
    h = auth.hash_password("hunter22!")
    assert auth.check_password("hunter22!", h) and not auth.check_password("nope", h)
    tok = auth.make_session(7)
    assert auth.read_session(tok) == 7
    assert auth.read_session(tok[:-1] + ("0" if tok[-1] != "0" else "1")) is None


def test_youtube_id_parsing():
    assert verify.youtube_id("https://youtube.com/shorts/AbCdEf12345") == "AbCdEf12345"
    assert verify.youtube_id("https://www.youtube.com/watch?v=xyz987654") == "xyz987654"
    assert verify.youtube_id("https://youtu.be/QQQQQQQ") == "QQQQQQQ"
    assert verify.platform_of("https://www.instagram.com/reel/C1/") == "instagram"


def test_ledger_caps_by_post_and_budget(db):
    uid = auth.signup(db, "b@x.test", "B", "password123", "brand")
    cid = db.insert("campaigns", {"owner_id": uid, "title": "T", "rate_cents_per_1k": 200, "max_cents_per_post": 3000,
                                  "min_views": 1000, "budget_cents": 5000, "created_at": "now"})
    ledger.record_funding(db, cid, 5000, "manual", "m1")   # net 4500 after 10%
    assert db.one("SELECT funded_cents, status FROM campaigns WHERE id=?", (cid,)) == {"funded_cents": 4500, "status": "active"}
    cl = auth.signup(db, "c@x.test", "C", "password123", "clipper")
    s1 = db.insert("submissions", {"campaign_id": cid, "clipper_id": cl, "platform": "youtube", "post_url": "u1",
                                   "status": "approved", "views": 500, "submitted_at": "now"})
    assert ledger.recalc_submission(db, s1) == 0            # under min views
    db.update("submissions", s1, views=10_000_000)
    assert ledger.recalc_submission(db, s1) == 3000         # per-post cap
    s2 = db.insert("submissions", {"campaign_id": cid, "clipper_id": cl, "platform": "youtube", "post_url": "u2",
                                   "status": "approved", "views": 10_000_000, "submitted_at": "now"})
    assert ledger.recalc_submission(db, s2) == 1500         # only what's left
    assert ledger.remaining_cents(db, cid) == 0
    assert db.one("SELECT status FROM campaigns WHERE id=?", (cid,))["status"] == "ended"
    assert ledger.balance_cents(db, cl)["available"] == 4500
    with pytest.raises(ledger.LedgerError):
        ledger.request_payout(db, cl, 999999)
    ledger.request_payout(db, cl, details="paypal")
    assert ledger.balance_cents(db, cl)["available"] == 0


def test_stripe_webhook_signature():
    body = b'{"x":1}'
    ts = int(time.time())
    sig = hmac.new(b"whsec_x", f"{ts}.".encode() + body, hashlib.sha256).hexdigest()
    assert stripe_api.verify_webhook(body, f"t={ts},v1={sig}", "whsec_x")
    assert not stripe_api.verify_webhook(body, f"t={ts},v1=bad", "whsec_x")
    assert not stripe_api.verify_webhook(body, f"t={ts - 9999},v1={sig}", "whsec_x")


def test_stripe_webhook_credits_once(db, monkeypatch):
    monkeypatch.setattr(rs, "stripe_webhook_secret", "whsec_t")
    uid = auth.signup(db, "b@x.test", "B", "password123", "brand")
    cid = db.insert("campaigns", {"owner_id": uid, "title": "T", "rate_cents_per_1k": 100, "budget_cents": 10000,
                                  "created_at": "now"})
    ledger.record_funding(db, cid, 10000, "stripe", reference="cs_1", paid=False)
    body = json.dumps({"type": "checkout.session.completed",
                       "data": {"object": {"id": "cs_1", "payment_status": "paid"}}}).encode()
    ts = int(time.time())
    sig = hmac.new(b"whsec_t", f"{ts}.".encode() + body, hashlib.sha256).hexdigest()
    c = TestClient(app)
    for _ in range(2):  # duplicate delivery must not double-credit
        assert c.post("/stripe/webhook", content=body, headers={"stripe-signature": f"t={ts},v1={sig}"}).status_code == 200
    assert db.one("SELECT funded_cents FROM campaigns WHERE id=?", (cid,))["funded_cents"] == 9000
    assert c.post("/stripe/webhook", content=body, headers={"stripe-signature": "t=1,v1=x"}).status_code == 400


# ---------------- full website flow ----------------

def test_end_to_end_youtube_campaign(db, monkeypatch):
    patch_http(monkeypatch, yt_transport())
    admin = client_for("boss@sxm.test", "clipper", "Stephen")
    assert db.one("SELECT role FROM users WHERE email='boss@sxm.test'")["role"] == "admin"
    brand = client_for("brand@x.test", "brand")
    clip = client_for("clip@x.test", "clipper")

    # brand creates a campaign
    tok = csrf_of(brand, "/campaigns/new")
    r = brand.post("/campaigns", data={"csrf": tok, "title": "Clip my streams", "rate": "2.00", "budget": "100",
                                       "min_views": "0", "max_per_post": "0", "platforms": ["youtube", "instagram"],
                                       "source_urls": "https://twitch.tv/x/videos"}, follow_redirects=False)
    cid = int(re.search(r"/campaigns/(\d+)", r.headers["location"]).group(1))
    # bad CSRF is refused
    assert brand.post(f"/campaigns/{cid}/status", data={"csrf": "x", "status": "paused"}).status_code == 400
    # drafts are hidden from others
    assert clip.get(f"/campaigns/{cid}").status_code == 404

    # without Stripe the brand is told to contact admin; admin records a manual payment
    r = brand.post(f"/campaigns/{cid}/fund", data={"csrf": tok, "amount": "100"})
    assert "Contact the site admin" in r.text
    atok = csrf_of(admin)
    admin.post(f"/campaigns/{cid}/fund", data={"csrf": atok, "amount": "100", "manual": "1"})
    assert db.one("SELECT funded_cents,status FROM campaigns WHERE id=?", (cid,)) == {"funded_cents": 9000, "status": "active"}
    assert "Clip my streams" in TestClient(app).get("/").text

    # clipper must add + verify their channel
    ctok = csrf_of(clip)
    r = clip.post(f"/campaigns/{cid}/submit", data={"csrf": ctok, "post_url": "https://youtube.com/shorts/abc123XYZ"})
    assert "Add your youtube account" in r.text
    clip.post("/profile/accounts", data={"csrf": ctok, "platform": "youtube", "handle": "@me"})
    db.run("UPDATE social_accounts SET code='SXM-CODE'")
    acc = db.one("SELECT id FROM social_accounts")["id"]
    r = clip.post(f"/profile/accounts/{acc}/check", data={"csrf": ctok})
    assert "Verified" in r.text
    r = clip.post(f"/campaigns/{cid}/submit", data={"csrf": ctok, "post_url": "https://youtube.com/shorts/abc123XYZ"})
    assert "Submitted" in r.text
    s = db.one("SELECT * FROM submissions")
    assert s["views"] == 25000 and s["views_source"] == "api"
    # duplicate submission refused
    assert "already submitted" in clip.post(f"/campaigns/{cid}/submit", data={
        "csrf": ctok, "post_url": "https://www.youtube.com/watch?v=abc123XYZ"}).text

    # another clipper can't review; brand approves -> earns 25k views * $2/1k = $50
    assert "Not your campaign" in clip.post(f"/submissions/{s['id']}/review", data={"csrf": ctok, "action": "approve"}).text \
        or clip.post(f"/submissions/{s['id']}/review", data={"csrf": ctok, "action": "approve"}).status_code == 403
    brand.post(f"/submissions/{s['id']}/review", data={"csrf": tok, "action": "approve"})
    assert db.one("SELECT earned_cents FROM submissions")["earned_cents"] == 5000

    # views grow; hourly refresh adds earnings but stops at the budget ($90 net)
    patch_http(monkeypatch, yt_transport(views=1_000_000))
    submissions.refresh_views(db)
    assert db.one("SELECT earned_cents FROM submissions")["earned_cents"] == 9000

    # payout
    r = clip.post("/payouts/request", data={"csrf": ctok, "details": "paypal: me@x.test"})
    assert "Payout requested" in r.text
    pid = db.one("SELECT id FROM payouts")["id"]
    admin.post(f"/admin/payouts/{pid}", data={"csrf": atok, "action": "manual", "reference": "PP-1"})
    assert ledger.balance_cents(db, db.one("SELECT id FROM users WHERE email='clip@x.test'")["id"]) == \
        {"earned": 9000, "paid": 9000, "pending_payout": 0, "available": 0}

    # every page renders for every role
    for c in (admin, brand, clip):
        for path in ("/", "/dashboard", "/profile", f"/campaigns/{cid}"):
            assert c.get(path).status_code == 200, path
    assert clip.get("/payouts").status_code == 200
    assert brand.get("/payouts").status_code == 403


def test_instagram_reported_views_need_brand_confirmation(db, monkeypatch, tmp_path):
    brand = client_for("brand@x.test", "brand")
    clip = client_for("clip@x.test", "clipper")
    bid = db.one("SELECT id FROM users WHERE email='brand@x.test'")["id"]
    cid = db.insert("campaigns", {"owner_id": bid, "title": "IG", "rate_cents_per_1k": 100, "budget_cents": 10000,
                                  "platforms": "instagram", "created_at": "now"})
    ledger.record_funding(db, cid, 10000, "manual", "m")
    ctok = csrf_of(clip)
    clip.post("/profile/accounts", data={"csrf": ctok, "platform": "instagram", "handle": "@me"})
    png = b"\x89PNG\r\n\x1a\n" + b"0" * 50
    r = clip.post(f"/campaigns/{cid}/submit", data={"csrf": ctok, "post_url": "https://www.instagram.com/reel/ABC/",
                                                    "views": "20000"}, files={"proof": ("s.png", png, "image/png")})
    assert "Submitted" in r.text
    s = db.one("SELECT * FROM submissions")
    assert s["views"] == 0 and s["reported_views"] == 20000 and s["proof_path"]
    assert brand.get(f"/proof/{s['id']}").status_code == 200
    btok = csrf_of(brand)
    brand.post(f"/submissions/{s['id']}/review", data={"csrf": btok, "action": "approve", "views": "18000"})
    assert db.one("SELECT views, earned_cents FROM submissions") == {"views": 18000, "earned_cents": 1800}
    # rejected file types
    r = clip.post(f"/campaigns/{cid}/submit", data={"csrf": ctok, "post_url": "https://www.instagram.com/reel/DEF/"},
                  files={"proof": ("x.html", b"<script>", "text/html")})
    assert "PNG, JPG or WebP" in r.text


# ---------------- SXM <-> SXM Campaigns ----------------

def test_sxm_marketo_auto_submits_to_rewards(db, monkeypatch, tmp_path):
    from sxm import campaigns as kc, campaign_site
    from sxm.config import settings as ks
    from sxm.db import DB as KDB, utcnow

    # Campaign site side: funded instagram campaign + clipper with an API token
    bid = auth.signup(db, "brand@x.test", "B", "password123", "brand")
    rcid = db.insert("campaigns", {"owner_id": bid, "title": "Streamer X", "rate_cents_per_1k": 150,
                                   "budget_cents": 10000, "platforms": "instagram",
                                   "source_urls": "https://twitch.tv/videos/1", "created_at": "now"})
    ledger.record_funding(db, rcid, 10000, "manual", "m")
    cl = auth.signup(db, "clip@x.test", "C", "password123", "clipper")
    verify.add_account(db, cl, "instagram", "@sxm")
    token = auth.new_api_token(db, cl)

    # SXM side, pointed at the campaign site in-process
    kdb = KDB(":memory:")
    monkeypatch.setattr(ks, "campaigns_site_url", "http://campaigns.test")
    monkeypatch.setattr(ks, "campaigns_site_token", token)
    tc = TestClient(app, base_url="http://campaigns.test")
    monkeypatch.setattr(campaign_site, "_client", lambda http=None: tc)

    assert campaign_site.sync_campaigns(kdb) == 1
    local = kdb.one("SELECT * FROM campaigns")
    assert local["site_campaign_id"] == rcid and local["rate_per_1k"] == 1.5
    sid = kdb.insert("sources", {"platform": "campaign", "external_id": "x", "url": "u", "campaign_id": local["id"],
                                 "found_at": utcnow()})
    clip = kdb.insert("clips", {"source_id": sid, "campaign_id": local["id"], "start_s": 0, "end_s": 30,
                                "status": "posted", "created_at": utcnow()})
    pid = kdb.insert("posts", {"clip_id": clip, "platform": "instagram", "scheduled_at": utcnow(), "status": "posted",
                               "permalink": "https://www.instagram.com/reel/AUTO1/", "posted_at": utcnow(),
                               "external_id": "m1"})
    kc.queue_submission(kdb, local["id"], pid)
    ks_sub = kdb.one("SELECT * FROM submissions")
    assert ks_sub["status"] == "submitted" and ks_sub["site_submission_id"]
    assert kc.pending_submissions(kdb) == []      # nothing left to paste by hand
    r_sub = db.one("SELECT * FROM submissions")
    assert r_sub["post_url"].endswith("/AUTO1/") and r_sub["clipper_id"] == cl

    # analytics sync pushes IG views (only visible to the account owner) for brand confirmation
    kdb.insert("metrics", {"post_id": pid, "captured_at": utcnow(), "views": 12345})
    assert campaign_site.push_views(kdb) == 1
    assert db.one("SELECT reported_views FROM submissions")["reported_views"] == 12345
