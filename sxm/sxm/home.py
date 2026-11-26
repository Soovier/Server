"""Everything the Home screen shows, in one place: setup steps, to-dos,
headline stats, pipeline counts and what's posting next."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from . import analytics, campaigns, review
from .config import settings
from .db import DB


def setup_steps(db: DB) -> list[dict]:
    yt = db.channels("youtube")
    meta_ch = db.channels("facebook") + db.channels("instagram")
    watching = review.permitted_streamers(db)
    steps = [
        {"key": "youtube", "title": "Connect your YouTube channels",
         "why": "So SXM can post Shorts and read views, watch time and revenue.",
         "done": any(c["enabled"] for c in yt),
         "detail": f"{sum(1 for c in yt if c['enabled'])} channel(s) posting" if yt else "",
         "action": "/oauth/google/start" if settings.google_client_id else "/setup",
         "cta": "Add a channel" if settings.google_client_id else "Add Google keys"},
        {"key": "streamers", "title": "Pick the streamers to clip",
         "why": "SXM only auto-posts streamers you have permission for.",
         "done": bool(watching), "detail": ", ".join(sorted(watching))[:80],
         "action": "/sources", "cta": "Add streamers"},
        {"key": "twitch", "title": "Turn on Twitch discovery",
         "why": "Finds new streams and top clips by itself every few hours.",
         "done": bool(settings.twitch_client_id and settings.twitch_client_secret),
         "detail": "", "action": "/setup", "cta": "How to"},
        {"key": "claude", "title": "Add Claude for smarter clips",
         "why": "Picks the best moments and writes hooks and captions.",
         "done": bool(settings.anthropic_api_key), "detail": settings.anthropic_model if settings.anthropic_api_key else "",
         "action": "/setup", "cta": "How to", "optional": True},
        {"key": "meta", "title": "Connect Instagram + Facebook",
         "why": "Post Reels to your Pages and Instagram accounts too.",
         "done": any(c["enabled"] for c in meta_ch),
         "detail": f"{sum(1 for c in meta_ch if c['enabled'])} account(s) posting" if meta_ch else "",
         "action": "/oauth/meta/start" if settings.meta_app_id else "/setup",
         "cta": "Connect" if settings.meta_app_id else "Later", "optional": True},
    ]
    return steps


def todos(db: DB) -> list[dict]:
    out = []
    pending = db.one("SELECT COUNT(*) n FROM clips WHERE status='pending_review'")["n"]
    if pending:
        out.append({"icon": "🎬", "title": f"{pending} clip{'s' if pending != 1 else ''} waiting for you",
                    "sub": "Watch, then swipe right to post or left to skip.", "href": "/review",
                    "cta": "Review", "tone": "accent", "count": pending})
    subs = campaigns.pending_submissions(db)
    if subs:
        soon = min(s["minutes_left"] for s in subs)
        out.append({"icon": "⏱", "title": f"{len(subs)} campaign post{'s' if len(subs) != 1 else ''} to submit",
                    "sub": f"{soon} min left on the first one." if soon else "The 30-minute window has passed.",
                    "href": "/campaigns", "cta": "Submit", "tone": "warn", "count": len(subs)})
    failed = db.one("SELECT COUNT(*) n FROM posts WHERE status='failed'")["n"]
    if failed:
        out.append({"icon": "⚠️", "title": f"{failed} post{'s' if failed != 1 else ''} failed",
                    "sub": "See why and retry.", "href": "/schedule", "cta": "Fix", "tone": "bad", "count": failed})
    stale = [c for c in db.channels() if c["enabled"] and not c.get("access_token")]
    if stale:
        out.append({"icon": "🔌", "title": f"Reconnect {stale[0]['name']}",
                    "sub": "Its login expired, so posts there are paused.", "href": "/setup", "cta": "Reconnect",
                    "tone": "bad", "count": len(stale)})
    waiting = db.one("SELECT COUNT(*) n FROM clips WHERE status='approved'")["n"]
    if waiting:
        out.append({"icon": "📭", "title": f"{waiting} approved clip{'s' if waiting != 1 else ''} with nowhere to post",
                    "sub": "Switch on a channel and they'll schedule themselves.", "href": "/setup",
                    "cta": "Channels", "tone": "warn", "count": waiting})
    return out


def pipeline(db: DB) -> list[dict]:
    def n(sql: str) -> int:
        return db.one(sql)["n"]
    return [
        {"key": "find", "label": "Found", "icon": "🔎", "n": n("SELECT COUNT(*) n FROM sources WHERE status IN ('new','downloading','downloaded')"),
         "hint": "streams waiting to be clipped"},
        {"key": "clip", "label": "Clipping", "icon": "✂️", "n": n("SELECT COUNT(*) n FROM clips WHERE status='rendering'"),
         "hint": "clips being edited"},
        {"key": "review", "label": "To review", "icon": "👀", "n": n("SELECT COUNT(*) n FROM clips WHERE status='pending_review'"),
         "hint": "need your OK"},
        {"key": "post", "label": "Scheduled", "icon": "📅", "n": n("SELECT COUNT(*) n FROM posts WHERE status IN ('scheduled','publishing')"),
         "hint": "posts lined up"},
        {"key": "live", "label": "Posted", "icon": "🚀", "n": n("SELECT COUNT(*) n FROM posts WHERE status='posted'"),
         "hint": "live on your channels"},
    ]


def upcoming(db: DB, limit: int = 6) -> list[dict]:
    tz = ZoneInfo(settings.timezone)
    rows = db.q("""SELECT p.id, p.platform, p.scheduled_at, c.id clip_id, c.hook, c.thumb_path, ch.name channel_name
                   FROM posts p JOIN clips c ON c.id=p.clip_id LEFT JOIN channels ch ON ch.id=p.channel_id
                   WHERE p.status IN ('scheduled','publishing') ORDER BY p.scheduled_at LIMIT ?""", (limit,))
    now = datetime.now(timezone.utc)
    for r in rows:
        at = datetime.fromisoformat(r["scheduled_at"])
        r["when"] = at.astimezone(tz).strftime("%a %I:%M %p").replace(" 0", " ")
        mins = int((at - now).total_seconds() // 60)
        r["in"] = "now" if mins <= 0 else (f"in {mins} min" if mins < 60 else
                                           f"in {mins // 60}h {mins % 60}m" if mins < 24 * 60 else f"in {mins // 1440}d")
    return rows


def last_run(db: DB) -> dict | None:
    j = db.one("SELECT * FROM job_log WHERE job IN ('discover','clip','auto_approve','publish') "
               "AND finished_at IS NOT NULL ORDER BY id DESC LIMIT 1")
    if not j:
        return None
    ago = datetime.now(timezone.utc) - datetime.fromisoformat(j["finished_at"])
    mins = int(ago.total_seconds() // 60)
    j["ago"] = "just now" if mins < 1 else (f"{mins} min ago" if mins < 60 else f"{mins // 60}h ago")
    return j


def state(db: DB, days: int = 7) -> dict:
    s = analytics.summary(db, days)
    prev = analytics.totals([r for r in analytics.post_rows(db, None, days * 2)
                             if r.get("posted_at") and r["posted_at"] <
                             (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()])
    c = s["combined"]

    def delta(cur: float, old: float) -> int | None:
        return None if not old else round((cur - old) / old * 100)
    daily: dict[str, int] = {}
    for d in s["daily"]:
        daily[d["day"]] = daily.get(d["day"], 0) + d["views"]
    steps = setup_steps(db)
    required = [x for x in steps if not x.get("optional")]
    return {
        "days": days,
        "stats": [
            {"key": "views", "label": "Views", "value": c["views"], "fmt": "num", "delta": delta(c["views"], prev["views"])},
            {"key": "watch", "label": "Watch hours", "value": c["watch_hours"], "fmt": "dec", "delta": delta(c["watch_hours"], prev["watch_hours"])},
            {"key": "income", "label": "Income", "value": c["total_income"], "fmt": "money", "delta": None},
            {"key": "posts", "label": "Posts", "value": c["posts"], "fmt": "num", "delta": delta(c["posts"], prev["posts"])},
        ],
        "spark": [daily[k] for k in sorted(daily)][-14:],
        "channels": [ch for ch in s["by_channel"] if ch["enabled"]],
        "steps": steps,
        "steps_done": sum(1 for x in required if x["done"]),
        "steps_total": len(required),
        "todos": todos(db),
        "pipeline": pipeline(db),
        "upcoming": upcoming(db),
        "last_run": last_run(db),
        "paused": db.get_setting("paused") == "1",
        "auto_approve": db.get_setting("auto_approve") == "1",
    }
