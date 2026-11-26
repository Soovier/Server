"""SXM dashboard (FastAPI). Run: python -m sxm"""
from __future__ import annotations

import hashlib
import hmac
import secrets
import threading
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote_plus
from zoneinfo import ZoneInfo

from fastapi import FastAPI, Form, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from . import analytics, campaigns, home, review
from .config import settings
from .db import get_db
from .discovery import add_manual_url
from .jobs import JOBS
from .publishers import meta, youtube

HERE = Path(__file__).parent
app = FastAPI(title="SXM")
app.mount("/static", StaticFiles(directory=HERE / "static"), name="static")
templates = Jinja2Templates(directory=HERE / "templates")
_oauth_states: set[str] = set()


def _local(iso: str | None, fmt: str = "%b %d, %I:%M %p") -> str:
    if not iso:
        return ""
    return datetime.fromisoformat(iso).astimezone(ZoneInfo(settings.timezone)).strftime(fmt)


templates.env.filters["local"] = _local
templates.env.filters["money"] = lambda v: f"${(v or 0):,.2f}"
templates.env.filters["num"] = lambda v: f"{int(v or 0):,}"
templates.env.filters["dec"] = lambda v: f"{(v or 0):,.1f}"


# ---------------- auth (optional password) ----------------

def _sig(value: str) -> str:
    return hmac.new(settings.session_secret.encode(), value.encode(), hashlib.sha256).hexdigest()


@app.middleware("http")
async def auth(request: Request, call_next):
    open_paths = ("/login", "/static", "/oauth")
    if settings.dashboard_password and not request.url.path.startswith(open_paths):
        if request.cookies.get("sxm") != _sig("ok"):
            return RedirectResponse("/login", 303)
    return await call_next(request)


@app.get("/login", response_class=HTMLResponse)
def login_page(request: Request):
    return templates.TemplateResponse(request, "login.html", {})


@app.post("/login")
def login(password: str = Form(...)):
    if not secrets.compare_digest(password, settings.dashboard_password or ""):
        return RedirectResponse("/login?bad=1", 303)
    r = RedirectResponse("/", 303)
    r.set_cookie("sxm", _sig("ok"), httponly=True, samesite="lax", max_age=60 * 60 * 24 * 30)
    return r


def page(request: Request, name: str, **ctx):
    db = get_db()
    ctx.setdefault("nav", name.split(".")[0])
    ctx["pending_count"] = db.one("SELECT COUNT(*) n FROM clips WHERE status='pending_review'")["n"]
    ctx["submit_count"] = len(campaigns.pending_submissions(db))
    ctx["auto_approve"] = db.get_setting("auto_approve") == "1"
    ctx["paused"] = db.get_setting("paused") == "1"
    return templates.TemplateResponse(request, name, ctx)


# ---------------- Dashboard ----------------

@app.get("/", response_class=HTMLResponse)
def home_page(request: Request, days: int = 7):
    return page(request, "home.html", nav="home", h=home.state(get_db(), days if days in (7, 30, 90) else 7),
                hello=_hello(), owner=settings.owner_name)


@app.get("/api/home")
def api_home(days: int = 7):
    return JSONResponse(home.state(get_db(), days if days in (7, 30, 90) else 7))


def _hello() -> str:
    hour = datetime.now(ZoneInfo(settings.timezone)).hour
    return "Good morning" if 5 <= hour < 12 else "Good afternoon" if hour < 17 else "Good evening"


@app.get("/stats", response_class=HTMLResponse)
def dashboard(request: Request, days: int = 30, channel: str = "", ch: int = 0):
    db = get_db()
    s = analytics.summary(db, days or None)
    rows = analytics.post_rows(db, channel or None, days or None, channel_id=ch or None)
    ch_name = (db.get_channel(ch) or {}).get("name", "") if ch else ""
    return page(request, "dashboard.html", nav="stats", s=s, rows=rows[:100], days=days, channel=channel, ch=ch,
                ch_name=ch_name)


@app.get("/api/summary")
def api_summary(days: int = 30):
    return JSONResponse(analytics.summary(get_db(), days or None))


# ---------------- Review queue ----------------

@app.get("/review", response_class=HTMLResponse)
def review_page(request: Request):
    db = get_db()
    clips = db.q("""SELECT c.*, s.streamer, s.title source_title, s.url source_url, k.name campaign
                    FROM clips c JOIN sources s ON s.id=c.source_id LEFT JOIN campaigns k ON k.id=c.campaign_id
                    WHERE c.status='pending_review' ORDER BY c.score DESC, c.id""")
    for c in clips:
        c["permitted"] = review.has_permission(db, c)
    return page(request, "review.html", clips=clips,
                min_score=db.get_setting("auto_approve_min_score", "0"))


@app.post("/review/{clip_id}")
def review_action(clip_id: int, action: str = Form(...), hook: str = Form(None), caption: str = Form(None),
                  hashtags: str = Form(None), note: str = Form("")):
    db = get_db()
    review.edit_copy(db, clip_id, hook=hook, caption=caption, hashtags=hashtags)
    if action == "approve":
        review.approve(db, clip_id, note)
    elif action == "reject":
        review.reject(db, clip_id, note)
    return RedirectResponse("/review", 303)


@app.post("/settings/auto-approve")
def toggle_auto(enabled: str = Form("0"), min_score: str = Form("0")):
    db = get_db()
    db.set_setting("auto_approve", "1" if enabled == "1" else "0")
    db.set_setting("auto_approve_min_score", min_score or "0")
    return RedirectResponse("/review", 303)


@app.get("/media/{kind}/{name}")
def media(kind: str, name: str):
    if kind not in ("clips",) or "/" in name or ".." in name:
        raise HTTPException(404)
    path = settings.media_dir / kind / name
    if not path.exists():
        raise HTTPException(404)
    return FileResponse(path)


# ---------------- Schedule ----------------

@app.get("/schedule", response_class=HTMLResponse)
def schedule_page(request: Request):
    db = get_db()
    posts = db.q("""SELECT p.*, c.hook, c.thumb_path, ch.name channel_name FROM posts p
                    JOIN clips c ON c.id=p.clip_id LEFT JOIN channels ch ON ch.id=p.channel_id
                    WHERE p.status IN ('scheduled','publishing','failed') ORDER BY p.scheduled_at""")
    jobs = db.q("SELECT * FROM job_log ORDER BY id DESC LIMIT 15")
    return page(request, "schedule.html", posts=posts, jobs=jobs, slots=settings.post_slots,
                tz=settings.timezone)


@app.post("/schedule/{post_id}")
def schedule_action(post_id: int, action: str = Form(...), when: str = Form("")):
    db = get_db()
    if action == "cancel":
        db.update("posts", post_id, status="cancelled")
    elif action == "now":
        db.update("posts", post_id, status="scheduled",
                  scheduled_at=datetime.now(timezone.utc).replace(microsecond=0).isoformat())
    elif action == "move" and when:
        local = datetime.fromisoformat(when).replace(tzinfo=ZoneInfo(settings.timezone))
        db.update("posts", post_id, status="scheduled",
                  scheduled_at=local.astimezone(timezone.utc).replace(microsecond=0).isoformat())
    return RedirectResponse("/schedule", 303)


@app.post("/jobs/{name}")
def run_job(name: str, back: str = Form("/schedule")):
    if name not in JOBS:
        raise HTTPException(404)
    threading.Thread(target=JOBS[name], daemon=True).start()
    return RedirectResponse(_safe_back(back) + ("&" if "?" in _safe_back(back) else "?") + "started=" + name, 303)


def _safe_back(back: str) -> str:
    return back if back.startswith("/") and not back.startswith("//") else "/"


@app.post("/settings/pause")
def pause(paused: str = Form("0"), back: str = Form("/schedule")):
    get_db().set_setting("paused", "1" if paused == "1" else "0")
    return RedirectResponse(_safe_back(back), 303)


# ---------------- Campaigns ----------------

@app.get("/campaigns", response_class=HTMLResponse)
def campaigns_page(request: Request):
    db = get_db()
    return page(request, "campaigns.html", pending=campaigns.pending_submissions(db),
                earnings=campaigns.estimated_earnings(db),
                camps=db.q("SELECT * FROM campaigns ORDER BY active DESC, id DESC"),
                history=db.q("""SELECT s.*, c.name campaign, p.permalink, p.platform FROM submissions s
                                JOIN campaigns c ON c.id=s.campaign_id JOIN posts p ON p.id=s.post_id
                                WHERE s.status!='ready' ORDER BY s.id DESC LIMIT 50"""))


@app.post("/campaigns")
def campaigns_add(name: str = Form(...), url: str = Form(...), source_urls: str = Form(""),
                  rate_per_1k: float = Form(0), max_payout: float = Form(0), platforms: list[str] = Form([]),
                  rules: str = Form(""), required_tags: str = Form("")):
    campaigns.add_campaign(get_db(), name=name, url=url, source_urls=source_urls, rate_per_1k=rate_per_1k,
                           max_payout=max_payout, platforms=",".join(platforms) or "instagram,youtube",
                           rules=rules, required_tags=required_tags)
    return RedirectResponse("/campaigns", 303)


@app.post("/campaigns/{cid}/toggle")
def campaigns_toggle(cid: int):
    db = get_db()
    c = db.one("SELECT active FROM campaigns WHERE id=?", (cid,))
    db.update("campaigns", cid, active=0 if c and c["active"] else 1)
    return RedirectResponse("/campaigns", 303)


@app.post("/submissions/{sid}")
def submission_action(sid: int, status: str = Form(...), payout: float = Form(None)):
    db = get_db()
    if status == "submitted":
        campaigns.mark_submitted(db, sid)
    else:
        campaigns.set_status(db, sid, status, payout)
    return RedirectResponse("/campaigns", 303)


# ---------------- Sources ----------------

@app.get("/sources", response_class=HTMLResponse)
def sources_page(request: Request):
    db = get_db()
    return page(request, "sources.html",
                sources=db.q("""SELECT s.*, c.name campaign FROM sources s LEFT JOIN campaigns c
                                ON c.id=s.campaign_id ORDER BY s.id DESC LIMIT 100"""),
                camps=db.q("SELECT id,name FROM campaigns WHERE active=1"),
                permitted=db.get_setting("permitted_streamers", ""),
                twitch=settings.twitch_streamers, yt=settings.youtube_source_channels)


@app.post("/sources")
def sources_add(url: str = Form(...), campaign_id: str = Form("")):
    add_manual_url(get_db(), url.strip(), int(campaign_id) if campaign_id else None)
    return RedirectResponse("/sources", 303)


@app.post("/sources/permitted")
def sources_permitted(permitted: str = Form("")):
    get_db().set_setting("permitted_streamers", permitted)
    return RedirectResponse("/sources", 303)


# ---------------- Setup / OAuth ----------------

@app.get("/setup", response_class=HTMLResponse)
def setup_page(request: Request, msg: str = "", pick: str = ""):
    db = get_db()
    chans = {p: db.channels(p) for p in review.PLATFORMS}
    return page(request, "setup.html", chans=chans, missing=settings.missing(), msg=msg, pick=pick,
                platforms=review.enabled_platforms(db), yt_privacy=db.get_setting("youtube_privacy", "public"),
                base_url=settings.base_url)


@app.post("/channels/{channel_id}")
def channel_action(channel_id: int, action: str = Form(...)):
    db = get_db()
    ch = db.get_channel(channel_id)
    if not ch:
        raise HTTPException(404)
    if action in ("on", "off"):
        db.set_channel_enabled(channel_id, action == "on")
        if action == "on":
            review.schedule_waiting(db)
        else:
            db.run("UPDATE posts SET status='cancelled' WHERE channel_id=? AND status='scheduled'", (channel_id,))
        msg = f"{ch['name']}: {'posting here' if action == 'on' else 'stopped posting here'}"
    elif action == "remove":
        db.remove_channel(channel_id)
        msg = f"Removed {ch['name']}"
    else:
        raise HTTPException(400)
    return RedirectResponse("/setup?msg=" + quote_plus(msg), 303)


@app.post("/settings/platforms")
def set_platforms(platforms: list[str] = Form([]), yt_privacy: str = Form("public")):
    db = get_db()
    db.set_setting("post_platforms", ",".join(p for p in platforms if p in review.PLATFORMS))
    db.set_setting("youtube_privacy", yt_privacy if yt_privacy in ("public", "unlisted", "private") else "public")
    return RedirectResponse("/setup", 303)


def _state() -> str:
    s = secrets.token_urlsafe(16)
    _oauth_states.add(s)
    return s


@app.get("/oauth/meta/start")
def meta_start():
    if not settings.meta_app_id:
        return RedirectResponse("/setup?msg=Add+META_APP_ID+and+META_APP_SECRET+to+.env+first", 303)
    return RedirectResponse(meta.login_url(_state()), 303)


@app.get("/oauth/meta/callback")
def meta_callback(code: str = "", state: str = "", error_description: str = ""):
    if error_description or state not in _oauth_states:
        return RedirectResponse(f"/setup?msg=Meta+login+failed:+{error_description or 'bad state'}", 303)
    _oauth_states.discard(state)
    try:
        res = meta.finish_login(get_db(), code)
        n_pg, n_ig = len(res["pages"]), len(res["instagram"])
        msg = (f"Found {n_pg} Facebook Page{'s' if n_pg != 1 else ''} and {n_ig} Instagram "
               f"account{'s' if n_ig != 1 else ''}.")
        if res["needs_choice"]:
            msg += " Switch on the ones you want SXM to post to."
            return RedirectResponse("/setup?pick=meta&msg=" + quote_plus(msg), 303)
    except Exception as e:
        msg = f"Meta error: {e}"
    return RedirectResponse("/setup?msg=" + quote_plus(msg), 303)


@app.get("/oauth/google/start")
def google_start():
    if not settings.google_client_id:
        return RedirectResponse("/setup?msg=Add+GOOGLE_CLIENT_ID+and+GOOGLE_CLIENT_SECRET+to+.env+first", 303)
    return RedirectResponse(youtube.login_url(_state()), 303)


@app.get("/oauth/google/callback")
def google_callback(code: str = "", state: str = "", error: str = ""):
    if error or state not in _oauth_states:
        return RedirectResponse(f"/setup?msg=Google+login+failed:+{error or 'bad state'}", 303)
    _oauth_states.discard(state)
    try:
        res = youtube.finish_login(get_db(), code)
        review.schedule_waiting(get_db())
        msg = f"Added YouTube channel {', '.join(res['channels'])}. To add another channel, click Add a channel again."
    except Exception as e:
        msg = f"YouTube error: {e}"
    return RedirectResponse("/setup?pick=youtube&msg=" + quote_plus(msg), 303)
