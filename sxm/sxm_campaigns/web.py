"""SXM Campaigns: the campaign marketplace website + API.

Run locally:  python -m sxm_campaigns          (http://localhost:8800)
"""
from __future__ import annotations

import secrets
from pathlib import Path
from urllib.parse import quote

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel

from . import auth, ledger, stripe_api, submissions, verify
from .config import settings
from .db import get_db, utcnow

HERE = Path(__file__).parent
app = FastAPI(title=settings.site_name)
app.mount("/static", StaticFiles(directory=HERE.parent / "sxm" / "static"), name="static")
templates = Jinja2Templates(directory=HERE / "templates")
templates.env.filters["usd"] = lambda c: f"${(c or 0) / 100:,.2f}"
templates.env.filters["num"] = lambda v: f"{int(v or 0):,}"
templates.env.filters["day"] = lambda iso: (iso or "")[:10]

MAX_PROOF_BYTES = 8 * 1024 * 1024
PROOF_TYPES = {"image/png": ".png", "image/jpeg": ".jpg", "image/webp": ".webp"}


# ---------------- helpers ----------------

def current_user(request: Request) -> dict | None:
    uid = auth.read_session(request.cookies.get("sxm_s"))
    if not uid:
        return None
    u = get_db().one("SELECT * FROM users WHERE id=?", (uid,))
    return u if u and not u["banned"] else None


def need(request: Request, *roles: str) -> dict:
    u = current_user(request)
    if not u:
        raise HTTPException(303, headers={"Location": "/login?next=" + quote(request.url.path)})
    if roles and u["role"] not in roles and u["role"] != "admin":
        raise HTTPException(403, "Not allowed")
    return u


def csrf_ok(user: dict, token: str) -> None:
    if not secrets.compare_digest(token or "", auth.csrf_token(user["id"])):
        raise HTTPException(400, "Form expired, go back and try again")


def back(path: str, msg: str = "", err: str = "") -> RedirectResponse:
    sep = "&" if "?" in path else "?"
    q = f"{sep}msg={quote(msg)}" if msg else (f"{sep}err={quote(err)}" if err else "")
    return RedirectResponse(path + q, 303)


def render(request: Request, name: str, **ctx):
    u = current_user(request)
    ctx.update(user=u, site=settings.site_name, csrf=auth.csrf_token(u["id"]) if u else "",
               msg=request.query_params.get("msg", ""), err=request.query_params.get("err", ""),
               stripe_on=settings.stripe_on, fee=settings.platform_fee_percent)
    return templates.TemplateResponse(request, name, ctx)


def dollars_to_cents(v: str | float) -> int:
    try:
        return int(round(float(v) * 100))
    except (TypeError, ValueError):
        return 0


async def save_proof(file: UploadFile | None) -> str | None:
    if not file or not file.filename:
        return None
    ext = PROOF_TYPES.get(file.content_type or "")
    if not ext:
        raise submissions.SubmitError("Screenshot must be PNG, JPG or WebP")
    data = await file.read()
    if len(data) > MAX_PROOF_BYTES:
        raise submissions.SubmitError("Screenshot is over 8 MB")
    settings.upload_dir.mkdir(parents=True, exist_ok=True)
    path = settings.upload_dir / (secrets.token_hex(12) + ext)
    path.write_bytes(data)
    return path.name


def campaign_stats(db, c: dict) -> dict:
    c = dict(c)
    s = db.one("""SELECT COUNT(*) n, SUM(status='pending') pending, SUM(status='approved') approved,
                  COALESCE(SUM(CASE WHEN status='approved' THEN views END),0) views FROM submissions
                  WHERE campaign_id=?""", (c["id"],))
    c.update(s)
    c["spent"] = ledger.spent_cents(db, c["id"])
    c["remaining"] = max(c["funded_cents"] - c["spent"], 0)
    c["pct"] = int(100 * c["spent"] / c["funded_cents"]) if c["funded_cents"] else 0
    return c


# ---------------- public ----------------

@app.get("/", response_class=HTMLResponse)
def home(request: Request):
    db = get_db()
    camps = [campaign_stats(db, c) for c in db.q("SELECT * FROM campaigns WHERE status='active' ORDER BY id DESC")]
    return render(request, "home.html", camps=camps, totals=ledger.platform_totals(db))


@app.get("/signup", response_class=HTMLResponse)
def signup_page(request: Request, role: str = "clipper"):
    return render(request, "auth.html", mode="signup", role=role)


@app.post("/signup")
def signup(email: str = Form(...), name: str = Form(""), password: str = Form(...), role: str = Form("clipper")):
    db = get_db()
    try:
        uid = auth.signup(db, email, name, password, role)
    except auth.AuthError as e:
        return back(f"/signup?role={quote(role)}", err=str(e))
    r = RedirectResponse("/dashboard", 303)
    r.set_cookie("sxm_s", auth.make_session(uid), httponly=True, samesite="lax", secure=settings.secure_cookies,
                 max_age=30 * 86400)
    return r


@app.get("/login", response_class=HTMLResponse)
def login_page(request: Request, next: str = "/dashboard"):
    return render(request, "auth.html", mode="login", next=next if next.startswith("/") else "/dashboard")


@app.post("/login")
def login(email: str = Form(...), password: str = Form(...), next: str = Form("/dashboard")):
    try:
        u = auth.login(get_db(), email, password)
    except auth.AuthError as e:
        return back("/login", err=str(e))
    r = RedirectResponse(next if next.startswith("/") and not next.startswith("//") else "/dashboard", 303)
    r.set_cookie("sxm_s", auth.make_session(u["id"]), httponly=True, samesite="lax", secure=settings.secure_cookies,
                 max_age=30 * 86400)
    return r


@app.post("/logout")
def logout():
    r = RedirectResponse("/", 303)
    r.delete_cookie("sxm_s")
    return r


# ---------------- dashboard ----------------

@app.get("/dashboard", response_class=HTMLResponse)
def dashboard(request: Request):
    u = need(request)
    db = get_db()
    ctx: dict = {}
    if u["role"] in ("brand", "admin"):
        where = "" if u["role"] == "admin" else "WHERE owner_id=?"
        params = () if u["role"] == "admin" else (u["id"],)
        ctx["camps"] = [campaign_stats(db, c) for c in db.q(f"SELECT * FROM campaigns {where} ORDER BY id DESC", params)]
    if u["role"] in ("clipper", "admin"):
        ctx["balance"] = ledger.balance_cents(db, u["id"])
        ctx["subs"] = db.q("""SELECT s.*, c.title campaign, c.rate_cents_per_1k FROM submissions s
                              JOIN campaigns c ON c.id=s.campaign_id WHERE s.clipper_id=? ORDER BY s.id DESC LIMIT 100""",
                           (u["id"],))
        ctx["accounts"] = db.q("SELECT * FROM social_accounts WHERE user_id=?", (u["id"],))
    if u["role"] == "admin":
        ctx["totals"] = ledger.platform_totals(db)
        ctx["payout_reqs"] = db.q("""SELECT p.*, u.name, u.email FROM payouts p JOIN users u ON u.id=p.clipper_id
                                     WHERE p.status='requested' ORDER BY p.id""")
        ctx["unverified"] = db.q("""SELECT a.*, u.name FROM social_accounts a JOIN users u ON u.id=a.user_id
                                    WHERE a.verified=0 ORDER BY a.id""")
        ctx["users"] = db.q("SELECT id,name,email,role,banned,created_at FROM users ORDER BY id DESC LIMIT 50")
    return render(request, "dashboard.html", **ctx)


# ---------------- campaigns ----------------

@app.get("/campaigns/new", response_class=HTMLResponse)
def campaign_new(request: Request):
    need(request, "brand")
    return render(request, "campaign_new.html")


@app.post("/campaigns")
def campaign_create(request: Request, csrf: str = Form(""), title: str = Form(...), description: str = Form(""),
                    source_urls: str = Form(""), rules: str = Form(""), required_tags: str = Form(""),
                    platforms: list[str] = Form([]), rate: str = Form(...), max_per_post: str = Form("0"),
                    min_views: int = Form(0), budget: str = Form(...), ends_at: str = Form("")):
    u = need(request, "brand")
    csrf_ok(u, csrf)
    rate_c, budget_c = dollars_to_cents(rate), dollars_to_cents(budget)
    plats = [p for p in platforms if p in verify.PLATFORMS] or ["youtube", "instagram"]
    if rate_c <= 0 or budget_c < 100:
        return back("/campaigns/new", err="Set a rate per 1K views and a budget of at least $1")
    cid = get_db().insert("campaigns", {
        "owner_id": u["id"], "title": title.strip()[:120], "description": description[:4000],
        "source_urls": source_urls[:4000], "rules": rules[:4000], "required_tags": required_tags[:300],
        "platforms": ",".join(plats), "rate_cents_per_1k": rate_c, "max_cents_per_post": dollars_to_cents(max_per_post),
        "min_views": max(min_views, 0), "budget_cents": budget_c, "ends_at": ends_at or None, "created_at": utcnow(),
    })
    return back(f"/campaigns/{cid}", msg="Campaign created. Fund it to start accepting clips.")


@app.get("/campaigns/{cid}", response_class=HTMLResponse)
def campaign_page(request: Request, cid: int):
    db = get_db()
    c = db.one("SELECT c.*, u.name owner FROM campaigns c JOIN users u ON u.id=c.owner_id WHERE c.id=?", (cid,))
    if not c:
        raise HTTPException(404)
    u = current_user(request)
    is_owner = bool(u and (u["id"] == c["owner_id"] or u["role"] == "admin"))
    if c["status"] == "draft" and not is_owner:
        raise HTTPException(404)
    subs = []
    if is_owner:
        subs = db.q("""SELECT s.*, u.name clipper FROM submissions s JOIN users u ON u.id=s.clipper_id
                       WHERE s.campaign_id=? ORDER BY (s.status='pending') DESC, s.id DESC""", (cid,))
    mine = db.q("SELECT * FROM submissions WHERE campaign_id=? AND clipper_id=? ORDER BY id DESC",
                (cid, u["id"])) if u else []
    fundings = db.q("SELECT * FROM fundings WHERE campaign_id=? ORDER BY id DESC", (cid,)) if is_owner else []
    return render(request, "campaign.html", c=campaign_stats(db, c), is_owner=is_owner, subs=subs, mine=mine,
                  fundings=fundings, window=settings.submit_window_hours)


@app.post("/campaigns/{cid}/fund")
def campaign_fund(request: Request, cid: int, csrf: str = Form(""), amount: str = Form(...),
                  manual: str = Form("")):
    u = need(request, "brand")
    csrf_ok(u, csrf)
    db = get_db()
    c = db.one("SELECT * FROM campaigns WHERE id=?", (cid,))
    if not c or (c["owner_id"] != u["id"] and u["role"] != "admin"):
        raise HTTPException(404)
    cents = dollars_to_cents(amount)
    if cents < 100:
        return back(f"/campaigns/{cid}", err="Minimum funding is $1")
    if manual and u["role"] == "admin":
        ledger.record_funding(db, cid, cents, "manual", reference=f"manual-{secrets.token_hex(6)}")
        return back(f"/campaigns/{cid}", msg="Funding recorded")
    if not settings.stripe_on:
        return back(f"/campaigns/{cid}", err="Card payments aren't set up yet. Contact the site admin to fund this campaign.")
    try:
        session = stripe_api.checkout_for_campaign(c, cents)
    except stripe_api.StripeError as e:
        return back(f"/campaigns/{cid}", err=f"Stripe: {e}")
    ledger.record_funding(db, cid, cents, "stripe", reference=session["id"], paid=False)
    return RedirectResponse(session["url"], 303)


@app.post("/campaigns/{cid}/status")
def campaign_status(request: Request, cid: int, csrf: str = Form(""), status: str = Form(...)):
    u = need(request, "brand")
    csrf_ok(u, csrf)
    db = get_db()
    c = db.one("SELECT * FROM campaigns WHERE id=?", (cid,))
    if not c or (c["owner_id"] != u["id"] and u["role"] != "admin"):
        raise HTTPException(404)
    if status not in ("active", "paused", "ended"):
        raise HTTPException(400)
    if status == "active" and ledger.remaining_cents(db, cid) <= 0:
        return back(f"/campaigns/{cid}", err="Add funds before activating")
    db.update("campaigns", cid, status=status)
    return back(f"/campaigns/{cid}", msg=f"Campaign {status}")


@app.post("/campaigns/{cid}/submit")
async def campaign_submit(request: Request, cid: int, csrf: str = Form(""), post_url: str = Form(...),
                          views: int = Form(0), proof: UploadFile | None = File(None)):
    u = need(request, "clipper")
    csrf_ok(u, csrf)
    try:
        proof_name = await save_proof(proof)
        submissions.submit(get_db(), u["id"], cid, post_url, views, proof_name)
    except submissions.SubmitError as e:
        return back(f"/campaigns/{cid}", err=str(e))
    return back(f"/campaigns/{cid}", msg="Submitted. The brand will review it.")


# ---------------- submissions ----------------

@app.post("/submissions/{sid}/review")
def submission_review(request: Request, sid: int, csrf: str = Form(""), action: str = Form(...),
                      views: str = Form(""), note: str = Form("")):
    u = need(request, "brand")
    csrf_ok(u, csrf)
    db = get_db()
    try:
        s = submissions.can_review(db, u, sid)
    except submissions.SubmitError as e:
        return back("/dashboard", err=str(e))
    try:
        if action == "approve":
            submissions.approve(db, u, sid, int(views) if views.strip().isdigit() else None, note)
        elif action == "reject":
            submissions.reject(db, u, sid, note)
        elif action == "views" and views.strip().isdigit():
            submissions.confirm_views(db, u, sid, int(views))
    except submissions.SubmitError as e:
        return back(f"/campaigns/{s['campaign_id']}", err=str(e))
    return back(f"/campaigns/{s['campaign_id']}", msg="Saved")


@app.post("/submissions/{sid}/report")
async def submission_report(request: Request, sid: int, csrf: str = Form(""), views: int = Form(...),
                            proof: UploadFile | None = File(None)):
    u = need(request, "clipper")
    csrf_ok(u, csrf)
    try:
        submissions.report_views(get_db(), u["id"], sid, views, await save_proof(proof))
    except submissions.SubmitError as e:
        return back("/dashboard", err=str(e))
    return back("/dashboard", msg="Views sent for confirmation")


@app.get("/proof/{sid}")
def proof(request: Request, sid: int):
    u = need(request)
    s = get_db().one("""SELECT s.proof_path, s.clipper_id, c.owner_id FROM submissions s
                        JOIN campaigns c ON c.id=s.campaign_id WHERE s.id=?""", (sid,))
    if not s or not s["proof_path"] or (u["id"] not in (s["clipper_id"], s["owner_id"]) and u["role"] != "admin"):
        raise HTTPException(404)
    return FileResponse(settings.upload_dir / Path(s["proof_path"]).name)


# ---------------- profile: social accounts + API token ----------------

@app.get("/profile", response_class=HTMLResponse)
def profile(request: Request, token: str = ""):
    u = need(request)
    accounts = get_db().q("SELECT * FROM social_accounts WHERE user_id=? ORDER BY id", (u["id"],))
    return render(request, "profile.html", accounts=accounts, new_token=token)


@app.post("/profile/accounts")
def profile_add_account(request: Request, csrf: str = Form(""), platform: str = Form(...), handle: str = Form(...)):
    u = need(request, "clipper")
    csrf_ok(u, csrf)
    try:
        verify.add_account(get_db(), u["id"], platform, handle)
    except verify.VerifyError as e:
        return back("/profile", err=str(e))
    return back("/profile", msg="Added. Put the code in your bio or channel description, then press Verify.")


@app.post("/profile/accounts/{aid}/check")
def profile_check_account(request: Request, aid: int, csrf: str = Form("")):
    u = need(request, "clipper")
    csrf_ok(u, csrf)
    db = get_db()
    acc = db.one("SELECT * FROM social_accounts WHERE id=? AND user_id=?", (aid, u["id"]))
    if not acc:
        raise HTTPException(404)
    try:
        ok = verify.check_account(db, aid)
    except verify.VerifyError as e:
        return back("/profile", err=str(e))
    if ok:
        return back("/profile", msg="Verified")
    if acc["platform"] in ("facebook", "tiktok"):
        return back("/profile", msg="The admin will check the code on your profile and verify it.")
    return back("/profile", err="Code not found yet. Save it in your bio/description and try again in a minute.")


@app.post("/profile/token")
def profile_token(request: Request, csrf: str = Form("")):
    u = need(request)
    csrf_ok(u, csrf)
    return RedirectResponse("/profile?token=" + auth.new_api_token(get_db(), u["id"]), 303)


# ---------------- payouts ----------------

@app.get("/payouts", response_class=HTMLResponse)
def payouts_page(request: Request):
    u = need(request, "clipper")
    db = get_db()
    return render(request, "payouts.html", balance=ledger.balance_cents(db, u["id"]),
                  payouts=db.q("SELECT * FROM payouts WHERE clipper_id=? ORDER BY id DESC", (u["id"],)),
                  min_payout=settings.min_payout_cents)


@app.post("/payouts/request")
def payouts_request(request: Request, csrf: str = Form(""), details: str = Form("")):
    u = need(request, "clipper")
    csrf_ok(u, csrf)
    try:
        ledger.request_payout(get_db(), u["id"], details=details)
    except ledger.LedgerError as e:
        return back("/payouts", err=str(e))
    return back("/payouts", msg="Payout requested")


@app.get("/payouts/stripe/start")
def payouts_stripe(request: Request):
    u = need(request, "clipper")
    if not settings.stripe_on:
        return back("/payouts", err="Stripe payouts aren't set up on this site yet")
    db = get_db()
    acct = u["stripe_account_id"]
    try:
        if not acct:
            acct = stripe_api.create_connect_account(u["email"])
            db.update("users", u["id"], stripe_account_id=acct)
        return RedirectResponse(stripe_api.onboarding_link(acct), 303)
    except stripe_api.StripeError as e:
        return back("/payouts", err=f"Stripe: {e}")


# ---------------- admin ----------------

@app.post("/admin/payouts/{pid}")
def admin_payout(request: Request, pid: int, csrf: str = Form(""), action: str = Form(...), reference: str = Form("")):
    u = need(request, "admin")
    if u["role"] != "admin":
        raise HTTPException(403)
    csrf_ok(u, csrf)
    db = get_db()
    p = db.one("SELECT p.*, u.stripe_account_id FROM payouts p JOIN users u ON u.id=p.clipper_id WHERE p.id=?", (pid,))
    if not p or p["status"] != "requested":
        return back("/dashboard", err="Payout not pending")
    if action == "reject":
        db.update("payouts", pid, status="rejected", reference=reference or None)
        return back("/dashboard", msg="Payout rejected; balance returned")
    if action == "stripe":
        if not (settings.stripe_on and p["stripe_account_id"]):
            return back("/dashboard", err="Clipper hasn't connected Stripe")
        try:
            reference = stripe_api.transfer(p["stripe_account_id"], p["amount_cents"], pid)
        except stripe_api.StripeError as e:
            return back("/dashboard", err=f"Stripe: {e}")
    db.update("payouts", pid, status="paid", paid_at=utcnow(), reference=reference or None,
              method="stripe" if action == "stripe" else "manual")
    return back("/dashboard", msg="Payout marked paid")


@app.post("/admin/accounts/{aid}/verify")
def admin_verify(request: Request, aid: int, csrf: str = Form("")):
    u = need(request, "admin")
    if u["role"] != "admin":
        raise HTTPException(403)
    csrf_ok(u, csrf)
    get_db().update("social_accounts", aid, verified=1, verified_by="admin")
    return back("/dashboard", msg="Account verified")


@app.post("/admin/users/{uid}/ban")
def admin_ban(request: Request, uid: int, csrf: str = Form(""), banned: int = Form(1)):
    u = need(request, "admin")
    if u["role"] != "admin" or uid == u["id"]:
        raise HTTPException(403)
    csrf_ok(u, csrf)
    get_db().update("users", uid, banned=1 if banned else 0)
    return back("/dashboard", msg="Updated")


# ---------------- Stripe webhook ----------------

@app.post("/stripe/webhook")
async def stripe_webhook(request: Request):
    payload = await request.body()
    if not stripe_api.verify_webhook(payload, request.headers.get("stripe-signature", "")):
        raise HTTPException(400, "bad signature")
    import json
    event = json.loads(payload)
    if event.get("type") == "checkout.session.completed":
        obj = event["data"]["object"]
        if obj.get("payment_status") == "paid":
            ledger.mark_funding_paid(get_db(), obj["id"])
    return {"ok": True}


# ---------------- JSON API (used by SXM) ----------------

def api_user(request: Request) -> dict:
    token = request.headers.get("authorization", "").removeprefix("Bearer ").strip()
    u = auth.user_for_token(get_db(), token)
    if not u:
        raise HTTPException(401, "Invalid API token")
    return u


class SubmitIn(BaseModel):
    campaign_id: int
    post_url: str
    views: int = 0
    posted_at: str | None = None


class ViewsIn(BaseModel):
    views: int


@app.get("/api/v1/me")
def api_me(request: Request):
    u = api_user(request)
    return {"id": u["id"], "name": u["name"], "role": u["role"], "balance": ledger.balance_cents(get_db(), u["id"])}


@app.get("/api/v1/campaigns")
def api_campaigns(request: Request):
    api_user(request)
    db = get_db()
    out = []
    for c in db.q("SELECT * FROM campaigns WHERE status='active' ORDER BY id"):
        out.append({k: c[k] for k in ("id", "title", "description", "source_urls", "rules", "required_tags",
                                      "platforms", "rate_cents_per_1k", "max_cents_per_post", "min_views", "ends_at")}
                   | {"url": f"{settings.base_url}/campaigns/{c['id']}",
                      "remaining_cents": ledger.remaining_cents(db, c["id"])})
    return out


@app.post("/api/v1/submissions")
def api_submit(request: Request, body: SubmitIn):
    u = api_user(request)
    if u["role"] not in ("clipper", "admin"):
        raise HTTPException(403, "Clipper accounts only")
    try:
        sid = submissions.submit(get_db(), u["id"], body.campaign_id, body.post_url, body.views, None, body.posted_at)
    except submissions.SubmitError as e:
        return JSONResponse({"error": str(e)}, status_code=422)
    return {"id": sid, "status": "pending"}


@app.post("/api/v1/submissions/{sid}/views")
def api_views(request: Request, sid: int, body: ViewsIn):
    u = api_user(request)
    try:
        submissions.report_views(get_db(), u["id"], sid, body.views)
    except submissions.SubmitError as e:
        return JSONResponse({"error": str(e)}, status_code=422)
    return {"ok": True}


@app.exception_handler(HTTPException)
async def http_errors(request: Request, exc: HTTPException):
    if exc.status_code == 303:
        return RedirectResponse(exc.headers["Location"], 303)
    if request.url.path.startswith("/api/"):
        return JSONResponse({"error": exc.detail}, status_code=exc.status_code)
    return HTMLResponse(f"<p style='font-family:sans-serif'>{exc.detail} · <a href='/'>home</a></p>",
                        status_code=exc.status_code)
