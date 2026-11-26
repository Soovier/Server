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

from . import (analytics, autopost, campaign_finder, campaigns, channel_stats, clip_edit, home, importer, inventory,
               review, streams, trending)
from starlette.concurrency import run_in_threadpool

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




def _asset_version() -> str:
    """Changes whenever a CSS/JS file changes, so browsers fetch the new one instead of a stale copy."""
    import hashlib
    h = hashlib.md5()
    for f in sorted((HERE / "static").glob("*.[cj]s")):
        h.update(f.read_bytes())
    return h.hexdigest()[:10]


templates.env.globals["asset_v"] = _asset_version()
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


@app.get("/stats")
def stats_redirect(request: Request):
    return RedirectResponse("/analytics" + (("?" + request.url.query) if request.url.query else ""), 307)


_stats_lock = threading.Lock()


def _sync_stats_bg() -> bool:
    if not _stats_lock.acquire(blocking=False):
        return False

    def run():
        try:
            channel_stats.sync_all(get_db())
        except Exception:
            import logging
            logging.getLogger("sxm.channel_stats").exception("sync")
        finally:
            _stats_lock.release()
    threading.Thread(target=run, name="sxm-stats", daemon=True).start()
    return True


def _ch_ids(ch: str) -> list[int]:
    return [int(x) for x in str(ch or "").split(",") if x.strip().isdigit()]


@app.get("/analytics", response_class=HTMLResponse)
def analytics_page(request: Request, days: int = 28, ch: str = "", msg: str = ""):
    db = get_db()
    days = days if days in (7, 28, 90, 365) else 28
    d = channel_stats.dashboard(db, days)
    if not d["synced"]:
        _sync_stats_bg()  # first visit: fetch the real numbers now
    d["syncing"] = _stats_lock.locked()
    s = analytics.summary(db, days)
    rows = analytics.post_rows(db, None, days)
    return page(request, "analytics.html", nav="analytics", d=d, s=s, rows=rows[:50], days=days,
                sel=_ch_ids(ch), msg=msg, syncing=d["syncing"])


@app.get("/api/analytics")
def api_analytics(days: int = 28):
    d = channel_stats.dashboard(get_db(), days if days in (7, 28, 90, 365) else 28)
    d["syncing"] = _stats_lock.locked()
    return JSONResponse(d)


@app.post("/analytics/refresh")
def analytics_refresh(back: str = Form("/analytics")):
    started = _sync_stats_bg()
    msg = "Pulling your latest numbers from every channel…" if started else "Already refreshing, hang on…"
    return RedirectResponse(_safe_back(back) + ("&" if "?" in _safe_back(back) else "?") + "msg=" + quote_plus(msg), 303)


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
        c["paste"] = review.paste_text(c)
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


@app.get("/download/{clip_id}")
def download_clip(clip_id: int):
    db = get_db()
    clip = db.one("SELECT * FROM clips WHERE id=?", (clip_id,))
    path = settings.media_dir / "clips" / f"clip_{clip_id}.mp4"
    if not clip or not path.exists():
        raise HTTPException(404)
    db.update("clips", clip_id, downloaded_at=datetime.now(timezone.utc).replace(microsecond=0).isoformat())
    return FileResponse(path, media_type="video/mp4", filename=review.download_name(clip))


@app.get("/downloads", response_class=HTMLResponse)
def downloads_page(request: Request, show: str = "all"):
    db = get_db()
    where = {"posted": "c.status='posted'", "ready": "c.status IN ('pending_review','approved','scheduled')",
             "new": "c.downloaded_at IS NULL"}.get(show, "1=1")
    clips = db.q(f"""SELECT c.*, s.streamer FROM clips c JOIN sources s ON s.id=c.source_id
                     WHERE c.video_path IS NOT NULL AND c.status NOT IN ('rejected','rendering','failed')
                     AND {where} ORDER BY c.id DESC LIMIT 60""")
    for c in clips:
        c["paste"] = review.paste_text(c)
        c["posts"] = db.q("""SELECT p.platform, p.permalink, p.status, p.scheduled_at, ch.name channel_name
                             FROM posts p LEFT JOIN channels ch ON ch.id=p.channel_id
                             WHERE p.clip_id=? AND p.status!='cancelled' ORDER BY p.scheduled_at""", (c["id"],))
        c["has_file"] = (settings.media_dir / "clips" / f"clip_{c['id']}.mp4").exists()
    return page(request, "downloads.html", nav="downloads", clips=clips, show=show)


@app.get("/media/{kind}/{name}")
def media(kind: str, name: str):
    if kind not in ("clips",) or "/" in name or ".." in name:
        raise HTTPException(404)
    path = settings.media_dir / kind / name
    if not path.exists():
        raise HTTPException(404)
    return FileResponse(path)


# ---------------- Streams (live + past streams + scan) ----------------

@app.get("/streams", response_class=HTMLResponse)
def streams_page(request: Request, p: str = "twitch", q: str = "", msg: str = ""):
    db = get_db()
    p = p if p in streams.PLATFORMS else "twitch"
    target = streams.parse_query(q, p) if q.strip() else None
    if target and target["kind"] == "creator":
        p = target["platform"]
    return page(request, "streams.html", nav="streams", p=p, q=q, target=target, favs=streams.favorites(db),
                fav=bool(target and target["kind"] == "creator" and streams.is_favorite(db, p, target["handle"])),
                host=request.headers.get("host", "localhost"), lim=_limits(db), msg=msg,
                layouts=LAYOUT_INFO)


@app.get("/api/streams/creator")
def api_creator(p: str, h: str, fresh: int = 0, request: Request = None):
    d = streams.creator(p, h, fresh=bool(fresh))
    host = request.headers.get("host", "localhost") if request else "localhost"
    if d.get("live"):
        d["embed"] = streams.embed_url(d["platform"], d["handle"], host, d["live"].get("video_id", ""))
    return JSONResponse(d)


@app.get("/api/streams/viral")
def api_viral(p: str = "all", fresh: int = 0):
    p = p if p in streams.PLATFORMS else "all"
    return JSONResponse(streams.viral(p, fresh=bool(fresh)))


@app.get("/api/streams/live")
def api_live_board():
    return JSONResponse(streams.live_board(get_db()))


@app.post("/streams/fav")
def streams_fav(p: str = Form(...), h: str = Form(...), name: str = Form(""), avatar: str = Form("")):
    added = streams.toggle_favorite(get_db(), p, h, name, avatar)
    return JSONResponse({"ok": True, "fav": added})


@app.post("/streams/scan")
def streams_scan(url: str = Form(...), title: str = Form(""), streamer: str = Form(""), platform: str = Form("manual"),
                 clips: int = Form(6), window: str = Form("any"), from_min: float = Form(0), to_min: float = Form(0),
                 duration: float = Form(0), prompt: str = Form(""), layout: str = Form("auto"),
                 min_s: int = Form(0), max_s: int = Form(0)):
    db = get_db()
    try:
        sid = streams.queue_scan(db, url, title=title, streamer=streamer, platform=platform, clips=clips,
                                 window=window, from_min=from_min, to_min=to_min, duration=duration, prompt=prompt,
                                 layout=layout, min_s=min_s, max_s=max_s)
    except ValueError as e:
        return RedirectResponse("/streams?msg=" + quote_plus(str(e)), 303)
    return RedirectResponse(f"/scan/{sid}", 303)


@app.get("/scan/{sid}", response_class=HTMLResponse)
def scan_page(request: Request, sid: int):
    st = streams.scan_status(get_db(), sid)
    if not st:
        raise HTTPException(404)
    return page(request, "scan.html", nav="streams", s=st)


@app.get("/api/scan/{sid}")
def api_scan(sid: int):
    st = streams.scan_status(get_db(), sid)
    if not st:
        raise HTTPException(404)
    return JSONResponse(st)


# ---------------- Clip editor ----------------

@app.get("/clip/{clip_id}", response_class=HTMLResponse)
def clip_page(request: Request, clip_id: int):
    st = clip_edit.state(get_db(), clip_id)
    if not st:
        raise HTTPException(404)
    return page(request, "clip.html", nav="review", c=st)


@app.get("/api/clip/{clip_id}")
def api_clip(clip_id: int):
    st = clip_edit.state(get_db(), clip_id)
    if not st:
        raise HTTPException(404)
    return JSONResponse(st)


@app.post("/api/clip/{clip_id}/save")
async def api_clip_save(clip_id: int, request: Request):
    body = await request.json()
    try:
        st = clip_edit.save(get_db(), clip_id, body or {}, note=str((body or {}).get("note", ""))[:200])
    except ValueError as e:
        return JSONResponse({"ok": False, "msg": str(e)}, 400)
    return JSONResponse({"ok": True, "msg": "Rendering your edit…", "state": {k: st[k] for k in (
        "start", "end", "layout", "aspect", "facecam", "game", "captions", "hook", "caption", "fps", "height")}})


@app.post("/api/clip/{clip_id}/ai")
async def api_clip_ai(clip_id: int, request: Request):
    body = await request.json()
    try:
        r = await run_in_threadpool(clip_edit.ai_edit, get_db(), clip_id, str((body or {}).get("ask", "")))
    except ValueError as e:
        return JSONResponse({"ok": False, "msg": str(e)}, 400)
    return JSONResponse({"ok": True, **r})


@app.post("/api/clip/{clip_id}/favorite")
def api_clip_fav(clip_id: int):
    db = get_db()
    c = db.one("SELECT favorite FROM clips WHERE id=?", (clip_id,))
    if not c:
        raise HTTPException(404)
    db.update("clips", clip_id, favorite=0 if c["favorite"] else 1)
    return JSONResponse({"ok": True, "favorite": not c["favorite"]})


@app.get("/clip/{clip_id}/source")
def clip_source_media(clip_id: int):
    db = get_db()
    c = db.one("SELECT * FROM clips WHERE id=?", (clip_id,))
    media, _ = clip_edit.media_of(db, c) if c else (None, 0)
    if not media:
        raise HTTPException(404)
    return FileResponse(media)


@app.get("/clip/{clip_id}/strip.jpg")
def clip_strip(clip_id: int):
    p = clip_edit.filmstrip(get_db(), clip_id)
    if not p:
        raise HTTPException(404)
    return FileResponse(p, headers={"Cache-Control": "no-cache"})


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


# ---------------- Import queue ----------------

LAYOUT_INFO = [
    ("auto", "Auto (recommended)", "Facecam on top + gameplay below when SXM finds a facecam, otherwise full frame."),
    ("stack", "Facecam + gameplay", "Streamer's camera on top third, gameplay filling the rest. The classic streamer Short."),
    ("blur", "Full frame", "The whole 16:9 picture in the middle over a blurred copy. Nothing gets cut off."),
    ("crop", "Center crop", "Zooms into the middle of the frame. Best for IRL or just-chatting streams."),
]


@app.get("/import", response_class=HTMLResponse)
def import_page(request: Request, msg: str = ""):
    db = get_db()
    return page(request, "import.html", nav="import", queue=importer.queue(db), layouts=LAYOUT_INFO,
                inbox=str(importer.inbox_dir()), import_layout=db.get_setting("import_layout", "auto"),
                import_clips=db.get_setting("import_clips", str(settings.clips_per_source)),
                permitted=sorted(review.permitted_streamers(db)), msg=msg,
                import_streamer=db.get_setting("import_streamer", ""), lim=_limits(db))


def _limits(db) -> dict:
    from .clipper import MAX_LEN, length_limits
    lo, hi = length_limits(db)
    return {"min": lo, "max": hi, "cap": MAX_LEN}


# ---------------- Campaign finder ----------------

@app.get("/find", response_class=HTMLResponse)
def find_page(request: Request, id: int = 0, msg: str = ""):
    db = get_db()
    f = campaign_finder.get(db, id) if id else None
    return page(request, "find.html", nav="campaigns", f=f, recent=campaign_finder.recent(db), layouts=LAYOUT_INFO,
                msg=msg, default_videos=campaign_finder.DEFAULT_VIDEOS, default_clips=campaign_finder.DEFAULT_CLIPS,
                lim=_limits(db),
                has_claude=bool(settings.anthropic_api_key))


@app.post("/find")
def find_start(url: str = Form(""), prompt: str = Form("")):
    try:
        fid = campaign_finder.start(get_db(), url, prompt)
    except ValueError as e:
        return RedirectResponse("/find?msg=" + quote_plus(str(e)), 303)
    return RedirectResponse(f"/find?id={fid}", 303)


@app.get("/api/find/{find_id}")
def find_status(find_id: int):
    f = campaign_finder.get(get_db(), find_id) or {}
    return JSONResponse({"status": f.get("status"), "step": f.get("step")})


@app.post("/find/{find_id}/make")
async def find_make(find_id: int, request: Request):
    form = await request.form()
    try:
        r = campaign_finder.make_clips(get_db(), find_id, form.getlist("pick"), layout=str(form.get("layout") or "stack"),
                                       total_clips=int(form.get("clips") or 10), auto_approve=form.get("auto") == "1",
                                       name=str(form.get("name") or ""), min_s=form.get("min_s"),
                                       max_s=form.get("max_s"))
    except ValueError as e:
        return RedirectResponse(f"/find?id={find_id}&msg=" + quote_plus(str(e)), 303)
    msg = f"✂ Making {r['clips']} clips from {r['videos']} videos. Watch them on Inventory."
    return RedirectResponse("/inventory?msg=" + quote_plus(msg), 303)


# ---------------- Inventory + Auto-post ----------------

@app.get("/inventory", response_class=HTMLResponse)
def inventory_page(request: Request, msg: str = ""):
    db = get_db()
    chans = [c for c in db.channels() if c.get("access_token")]
    return page(request, "inventory.html", nav="inventory", b=inventory.board(db), rules=autopost.summary(db),
                channels=chans, sources=autopost.SOURCES, msg=msg, lim=_limits(db))


@app.post("/autopost")
def autopost_save(channel_id: int = Form(...), every_hours: float = Form(4), source: str = Form("library"),
                  min_s: int = Form(0), max_s: int = Form(0)):
    try:
        autopost.save_rule(get_db(), channel_id, every_hours, source, min_s=min_s, max_s=max_s)
        msg = "🤖 Auto-post is on. The first post is lined up."
    except ValueError as e:
        msg = str(e)
    return RedirectResponse("/inventory?msg=" + quote_plus(msg), 303)


@app.post("/autopost/{rule_id}/{action}")
def autopost_action(rule_id: int, action: str):
    db = get_db()
    if action in ("on", "off"):
        autopost.set_enabled(db, rule_id, action == "on")
    elif action == "remove":
        autopost.remove_rule(db, rule_id)
    else:
        raise HTTPException(404)
    return RedirectResponse("/inventory", 303)


@app.post("/inventory/premade")
async def premade_upload(request: Request, name: str):
    dest = importer.uploads_dir() / ("premade-" + importer.safe_name(name))
    part = dest.with_suffix(dest.suffix + ".part")
    try:
        with part.open("wb") as f:
            async for chunk in request.stream():
                f.write(chunk)
        part.rename(dest)
    except Exception:
        part.unlink(missing_ok=True)
        raise
    cid = autopost.add_premade(get_db(), dest, title=Path(name).stem.replace("_", " "))
    return JSONResponse({"ok": True, "id": cid})


# ---------------- Trending ----------------

_trend_lock = threading.Lock()
_trend_tried = {"at": 0.0}


def _refresh_trending_bg(force: bool = False) -> bool:
    """Refresh in the background so the page never waits on YouTube. False if one is already running
    (or, when not forced, if one was tried in the last 10 minutes)."""
    import time
    if not force and time.time() - _trend_tried["at"] < 600:
        return False
    if not _trend_lock.acquire(blocking=False):
        return False
    _trend_tried["at"] = time.time()

    def run():
        try:
            trending.refresh(get_db(), force=force)
        except Exception:
            import logging
            logging.getLogger("sxm.trending").exception("refresh")
        finally:
            _trend_lock.release()
    threading.Thread(target=run, name="sxm-trending", daemon=True).start()
    return True


@app.get("/trending", response_class=HTMLResponse)
def trending_page(request: Request, msg: str = ""):
    db = get_db()
    data = trending.cached(db)
    if trending.is_stale(data):
        _refresh_trending_bg()
    return page(request, "trending.html", nav="trending", t=trending.view(db, data), loading=_trend_lock.locked(),
                fetched_at=(data or {}).get("fetched_at", ""), msg=msg)


@app.get("/api/trending")
def api_trending():
    data = trending.cached(get_db()) or {}
    return JSONResponse({"fetched_at": data.get("fetched_at", ""), "loading": _trend_lock.locked()})


@app.post("/trending/refresh")
def trending_refresh():
    _refresh_trending_bg(force=True)
    return RedirectResponse("/trending?msg=" + quote_plus("Checking what's hot. New cards appear in a few seconds."), 303)


@app.post("/trending/clip")
def trending_clip(url: str = Form(...), title: str = Form(""), streamer: str = Form("")):
    try:
        sid = trending.queue_clip(get_db(), url, title, streamer)
    except ValueError as e:
        return JSONResponse({"ok": False, "msg": str(e)}, 400)
    return JSONResponse({"ok": True, "id": sid,
                         "msg": "Sent to the Import queue" if sid else "Already in your queue"})


@app.get("/api/queue")
def api_queue():
    rows = importer.queue(get_db())
    keep = ("id", "name", "streamer", "status", "state", "progress", "clips_made", "to_review", "position",
            "length", "error", "layout", "clips_wanted")
    return JSONResponse([{k: r.get(k) for k in keep} for r in rows])


@app.post("/import/upload")
async def import_upload(request: Request, name: str, streamer: str = "", layout: str = "auto", clips: int = 0,
                        min_s: int = 0, max_s: int = 0):
    dest = importer.uploads_dir() / importer.safe_name(name)
    n = 1
    while dest.exists():
        dest = dest.with_name(f"{Path(importer.safe_name(name)).stem}-{n}{dest.suffix}")
        n += 1
    part = dest.with_suffix(dest.suffix + ".part")
    try:
        with part.open("wb") as f:
            async for chunk in request.stream():
                f.write(chunk)
        part.rename(dest)
    except Exception:
        part.unlink(missing_ok=True)
        raise
    db = get_db()
    _remember_import_prefs(db, streamer, layout, clips, min_s, max_s)
    sid = importer.add_file(db, dest, streamer=streamer, layout=layout, clips=clips,
                            title=Path(name).stem.replace("_", " "), min_s=min_s, max_s=max_s)
    return JSONResponse({"ok": True, "id": sid, "file": dest.name})


@app.post("/import/links")
def import_links(links: str = Form(""), streamer: str = Form(""), layout: str = Form("auto"), clips: int = Form(0),
                 min_s: int = Form(0), max_s: int = Form(0)):
    db = get_db()
    _remember_import_prefs(db, streamer, layout, clips, min_s, max_s)
    ids = importer.add_links(db, links, streamer=streamer, layout=layout, clips=clips, min_s=min_s, max_s=max_s)
    msg = f"Added {len(ids)} video{'s' if len(ids) != 1 else ''} to the queue" if ids else "No new links found"
    return RedirectResponse("/import?msg=" + quote_plus(msg), 303)


@app.post("/import/{source_id}/{action}")
def import_action(source_id: int, action: str):
    db = get_db()
    if action == "retry":
        importer.retry(db, source_id)
    elif action == "remove":
        importer.remove(db, source_id)
    elif action == "cancel":
        importer.cancel(db, source_id)
    else:
        raise HTTPException(404)
    return RedirectResponse("/import", 303)


@app.post("/api/cancel/{source_id}")
def api_cancel(source_id: int):
    ok = importer.cancel(get_db(), source_id)
    return JSONResponse({"ok": ok, "msg": "Cancelled" if ok else "Nothing to cancel"})


def _remember_import_prefs(db, streamer: str, layout: str, clips: int, min_s: int = 0, max_s: int = 0) -> None:
    ln = importer.lengths(min_s, max_s)
    if ln.get("min_s"):
        db.set_setting("clip_min_s", str(ln["min_s"]))
    if ln.get("max_s"):
        db.set_setting("clip_max_s", str(ln["max_s"]))
    if layout in importer.LAYOUTS:
        db.set_setting("import_layout", layout)
    if clips:
        db.set_setting("import_clips", str(max(1, min(int(clips), 15))))
    if streamer.strip():
        db.set_setting("import_streamer", streamer.strip())


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
