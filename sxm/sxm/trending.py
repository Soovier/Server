"""Trending: what's blowing up right now, so you clip the videos that get views.

Three carousels, each refreshed every few hours and cached so the YouTube
quota lasts (one refresh costs about 500 of the 10,000 free daily units):

    streamers  stream VODs + highlight uploads from the last 3 days, plus
               Twitch's most-watched clips from the last 24 hours
    gta        GTA right now (this week, by views per hour) and of all time
    games      the YouTube Gaming chart and Twitch's top games right now,
               plus the most-viewed gaming videos of all time

"Right now" is ranked by views per hour since upload, which is what makes a
video spread, not raw views. Kick has no public API, so it's linked, not pulled.
"""
from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx

from .config import settings
from .db import DB
from .discovery import TWITCH_API, YT_API, Twitch, _parse_iso_duration

log = logging.getLogger("sxm.trending")

CACHE_KEY = "trending_cache"
GTA_TWITCH_ID = "32982"  # Grand Theft Auto V
PER_ROW = 7
STREAM_QUERY = "stream highlights|livestream|twitch stream|kick stream|full stream"
GTA_QUERY = "GTA|GTA 6|GTA RP|Grand Theft Auto"
GTA_RE = re.compile(r"\b(gta|grand theft auto)\b", re.I)
STOP = {"the", "and", "for", "clips", "clip", "clipping", "campaign", "campaigns", "official", "channel",
        "with", "from", "stream", "streams", "gaming", "youtube", "twitch", "kick", "shorts", "www", "com", "http", "https"}

SECTIONS = [
    {"key": "streamers", "title": "Streamer videos blowing up now", "icon": "🔥",
     "sub": "Streams and highlights from the last 3 days, ranked by views per hour."},
    {"key": "gta", "title": "GTA", "icon": "🚗",
     "sub": "The GTA videos taking off this week, and the biggest ones ever."},
    {"key": "games", "title": "Other games going viral", "icon": "🎮",
     "sub": "What's charting in Gaming right now, and the all-time giants."},
]


class NoKey(Exception):
    pass


# ---------------- helpers ----------------

def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse(ts: str | None) -> datetime | None:
    if not ts:
        return None
    try:
        return datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except ValueError:
        return None


def age_text(ts: str | None, now: datetime | None = None) -> str:
    dt = _parse(ts)
    if not dt:
        return ""
    s = max(0, ((now or _now()) - dt).total_seconds())
    if s < 3600:
        return f"{int(s // 60) or 1}m ago"
    if s < 86400:
        return f"{int(s // 3600)}h ago"
    if s < 86400 * 60:
        return f"{int(s // 86400)}d ago"
    if s < 86400 * 730:
        return f"{int(s // (86400 * 30))}mo ago"
    return f"{int(s // (86400 * 365))}y ago"


def short_num(n: float | int | None) -> str:
    n = float(n or 0)
    for div, suf in ((1e9, "B"), (1e6, "M"), (1e3, "K")):
        if n >= div:
            v = n / div
            return f"{v:.1f}".rstrip("0").rstrip(".") + suf
    return str(int(n))


def views_per_hour(views: int, published: str | None, now: datetime | None = None) -> float:
    dt = _parse(published)
    if not dt:
        return 0.0
    hours = max(1.0, ((now or _now()) - dt).total_seconds() / 3600)
    return views / hours


# ---------------- YouTube ----------------

class YT:
    """Read-only YouTube Data API calls with an API key or your channel login."""

    def __init__(self, db: DB, http: httpx.Client | None = None):
        self.http = http or httpx.Client(timeout=30)
        self.key = settings.youtube_api_key
        self.headers: dict[str, str] = {}
        if not self.key:
            if not db.get_account("youtube"):
                raise NoKey("Connect a YouTube channel (or add YOUTUBE_API_KEY to .env) to see trending videos.")
            from .publishers.youtube import ensure_fresh_token
            self.headers = {"Authorization": f"Bearer {ensure_fresh_token(db, http=self.http)}"}

    def get(self, path: str, **params: Any) -> list[dict]:
        if self.key:
            params["key"] = self.key
        r = self.http.get(f"{YT_API}/{path}", params=params, headers=self.headers)
        r.raise_for_status()
        return r.json().get("items", [])

    def search(self, q: str, order: str, days: int | None = None, n: int = 25) -> list[str]:
        params: dict[str, Any] = {"part": "id", "type": "video", "q": q, "order": order, "maxResults": n,
                                  "videoCategoryId": "20", "regionCode": "US", "relevanceLanguage": "en",
                                  "safeSearch": "moderate"}
        if days:
            params["publishedAfter"] = _iso(_now() - timedelta(days=days))
        return [i["id"]["videoId"] for i in self.get("search", **params) if i.get("id", {}).get("videoId")]

    def videos(self, ids: list[str]) -> list[dict]:
        out = []
        for i in range(0, len(ids), 50):
            chunk = [x for x in ids[i:i + 50] if x]
            if chunk:
                out += self.get("videos", part="snippet,statistics,contentDetails,liveStreamingDetails",
                                id=",".join(chunk))
        return [card_from_youtube(v) for v in out]

    def chart(self, n: int = 50) -> list[dict]:
        items = self.get("videos", part="snippet,statistics,contentDetails,liveStreamingDetails",
                         chart="mostPopular", videoCategoryId="20", regionCode="US", maxResults=n)
        return [card_from_youtube(v) for v in items]


def card_from_youtube(v: dict) -> dict:
    sn, st, cd = v.get("snippet", {}), v.get("statistics", {}), v.get("contentDetails", {})
    thumbs = sn.get("thumbnails", {})
    thumb = (thumbs.get("maxres") or thumbs.get("high") or thumbs.get("medium") or thumbs.get("default") or {}).get("url", "")
    live = v.get("liveStreamingDetails") or {}
    return {
        "platform": "youtube", "id": v["id"], "url": f"https://www.youtube.com/watch?v={v['id']}",
        "title": sn.get("title", ""), "channel": sn.get("channelTitle", ""), "thumb": thumb,
        "published": sn.get("publishedAt"), "views": int(st.get("viewCount", 0) or 0),
        "likes": int(st.get("likeCount", 0) or 0), "duration_s": _parse_iso_duration(cd.get("duration", "PT0S")),
        "hd": cd.get("definition") == "hd", "was_live": bool(live.get("actualStartTime")),
        "live_now": sn.get("liveBroadcastContent") == "live",
    }


# ---------------- Twitch ----------------

def card_from_twitch_clip(c: dict, game: str = "") -> dict:
    return {
        "platform": "twitch", "id": c["id"], "url": c["url"], "title": c.get("title", ""),
        "channel": c.get("broadcaster_name", ""), "thumb": c.get("thumbnail_url", ""),
        "published": c.get("created_at"), "views": int(c.get("view_count", 0) or 0), "likes": 0,
        "duration_s": float(c.get("duration") or 0), "hd": True, "was_live": True, "live_now": False,
        "game": game,
    }


def twitch_cards(tw: Twitch, game_id: str | None = None, hours: int = 24, n: int = 10, game: str = "") -> list[dict]:
    params: dict[str, Any] = {"started_at": _iso(_now() - timedelta(hours=hours)), "first": n}
    if game_id:
        params["game_id"] = game_id
    return [card_from_twitch_clip(c, game) for c in tw.get("clips", **params)]


def twitch_top_games(tw: Twitch, n: int = 10) -> list[dict]:
    return tw.get("games/top", first=n)


# ---------------- ranking + building ----------------

def _rank_now(cards: list[dict], now: datetime) -> list[dict]:
    for c in cards:
        c["vph"] = views_per_hour(c["views"], c.get("published"), now)
    return sorted(cards, key=lambda c: c["vph"], reverse=True)


def _dedupe(cards: list[dict], seen: set[str] | None = None, per_channel: int = 2) -> list[dict]:
    """One video per id, at most two per channel, so a row isn't one creator."""
    seen = seen or set()
    mine: set[str] = set()
    count: dict[str, int] = {}
    out = []
    for c in cards:
        key = f"{c['platform']}:{c['id']}"
        ch = (c.get("channel") or "").lower()
        if key in seen or key in mine or count.get(ch, 0) >= per_channel:
            continue
        mine.add(key)
        count[ch] = count.get(ch, 0) + 1
        out.append(c)
    return out


def _take(row: list[dict], seen: set[str]) -> list[dict]:
    """Mark what a row shows so the same video isn't in two rows."""
    seen.update(f"{c['platform']}:{c['id']}" for c in row)
    return row


def _mix(a: list[dict], b: list[dict], n: int) -> list[dict]:
    """Interleave YouTube and Twitch so both show up in a row."""
    out = []
    ia, ib = iter(a), iter(b)
    while len(out) < n:
        took = False
        for it, k in ((ia, 2), (ib, 1)):
            for _ in range(k):
                x = next(it, None)
                if x is not None and len(out) < n:
                    out.append(x)
                    took = True
        if not took:
            break
    return out


def _good_youtube(c: dict, min_s: int = 180) -> bool:
    return c["hd"] and c["duration_s"] >= min_s and c["views"] > 0


def build(db: DB, http: httpx.Client | None = None) -> dict:
    """Fetch every row. Each source fails on its own so one outage doesn't blank the page."""
    now = _now()
    rows: dict[str, dict[str, list[dict]]] = {s["key"]: {"now": [], "all": []} for s in SECTIONS}
    notes: list[str] = []
    seen: set[str] = set()

    yt = None
    try:
        yt = YT(db, http)
    except NoKey as e:
        notes.append(str(e))
    except Exception as e:
        notes.append(f"YouTube sign-in failed: {e}")

    tw = None
    if settings.twitch_client_id and settings.twitch_client_secret:
        tw = Twitch(http)

    def safe(label: str, fn, default):
        try:
            return fn()
        except Exception as e:
            log.warning("trending %s failed: %s", label, e)
            notes.append(f"{label} couldn't load ({type(e).__name__}).")
            return default

    # 1) Streamer videos from the last 3 days
    yt_stream = []
    if yt:
        yt_stream = safe("YouTube streams", lambda: yt.videos(yt.search(STREAM_QUERY, "viewCount", days=3, n=40)), [])
        yt_stream = _rank_now([c for c in yt_stream if _good_youtube(c, 240)], now)
        # actual stream VODs first among equals: they're the ones you can mine for moments
        yt_stream.sort(key=lambda c: c["vph"] * (1.3 if c["was_live"] else 1), reverse=True)
    # Twitch only lists clips per game or per streamer, so take the top clips of today's biggest categories.
    top_games = safe("Twitch top games", lambda: twitch_top_games(tw), []) if tw else []
    tw_top = []
    for g in [g for g in top_games if g.get("id") != GTA_TWITCH_ID][:3]:  # GTA has its own row
        tw_top += safe(f"Twitch {g.get('name')}", lambda g=g: twitch_cards(tw, g["id"], hours=24, n=4, game=g["name"]), [])[:1]
    tw_top = _rank_now(tw_top, now)
    rows["streamers"]["now"] = _take(_mix(_dedupe(yt_stream), _dedupe(tw_top), PER_ROW), seen)

    # 2) GTA: this week by velocity, and all time by views
    if yt:
        gta_now = safe("GTA this week", lambda: yt.videos(yt.search(GTA_QUERY, "viewCount", days=7, n=30)), [])
        gta_now = _rank_now([c for c in gta_now if _good_youtube(c, 60) and GTA_RE.search(c["title"])], now)
        gta_all = safe("GTA all-time", lambda: yt.videos(yt.search(GTA_QUERY, "viewCount", n=30)), [])
        gta_all = sorted([c for c in gta_all if _good_youtube(c, 60)], key=lambda c: c["views"], reverse=True)
    else:
        gta_now, gta_all = [], []
    tw_gta = _rank_now(safe("Twitch GTA clips", lambda: twitch_cards(tw, GTA_TWITCH_ID, hours=72, n=10, game="GTA V"), []), now) if tw else []
    rows["gta"]["now"] = _take(_mix(_dedupe(gta_now, seen), _dedupe(tw_gta, seen), PER_ROW), seen)
    rows["gta"]["all"] = _take(_dedupe(gta_all, seen)[:PER_ROW], seen)

    # 3) Other games: the Gaming chart right now, Twitch's top games, and all-time giants
    if yt:
        chart = safe("YouTube Gaming chart", lambda: yt.chart(50), [])
        chart = _rank_now([c for c in chart if _good_youtube(c, 120) and not GTA_RE.search(c["title"])], now)
        big = safe("Gaming all-time", lambda: yt.videos(yt.search("gameplay|stream|highlights", "viewCount", n=30)), [])
        big = sorted([c for c in big if _good_youtube(c, 120) and not GTA_RE.search(c["title"])],
                     key=lambda c: c["views"], reverse=True)
    else:
        chart, big = [], []
    tw_games = []
    if tw:
        for g in top_games:
            if g.get("id") == GTA_TWITCH_ID or g.get("name") == "Just Chatting":
                continue
            got = safe(f"Twitch {g.get('name')}", lambda g=g: twitch_cards(tw, g["id"], hours=24, n=6, game=g["name"]), [])
            tw_games += [c for c in got if f"twitch:{c['id']}" not in seen][:1]
        tw_games = _rank_now(tw_games, now)
    rows["games"]["now"] = _take(_mix(_dedupe(chart, seen), _dedupe(tw_games, seen, per_channel=1), PER_ROW), seen)
    rows["games"]["all"] = _take(_dedupe(big, seen)[:PER_ROW], seen)

    return {"fetched_at": _iso(now), "rows": rows, "notes": notes,
            "sources": {"youtube": bool(yt), "twitch": bool(tw)}}


# ---------------- cache + view ----------------

def cached(db: DB) -> dict | None:
    raw = db.get_setting(CACHE_KEY)
    if not raw:
        return None
    try:
        return json.loads(raw)
    except ValueError:
        return None


def is_stale(data: dict | None, hours: float | None = None) -> bool:
    if not data:
        return True
    hours = hours if hours is not None else settings.trending_refresh_hours
    dt = _parse(data.get("fetched_at"))
    return not dt or _now() - dt > timedelta(hours=hours)


def refresh(db: DB, http: httpx.Client | None = None, force: bool = False) -> dict:
    data = cached(db)
    # A forced refresh is still held to once per 20 minutes so a click-happy day can't burn the quota.
    if force and data and not is_stale(data, hours=1 / 3):
        return data
    if not force and not is_stale(data):
        return data  # type: ignore[return-value]
    fresh = build(db, http)
    got = sum(len(v) for r in fresh["rows"].values() for v in r.values())
    if got or not data:
        db.set_setting(CACHE_KEY, json.dumps(fresh))
        return fresh
    data["notes"] = fresh["notes"]  # keep yesterday's cards rather than an empty page
    return data


def campaign_match(card: dict, camps: list[dict]) -> dict | None:
    hay = f"{card.get('title', '')} {card.get('channel', '')} {card.get('url', '')}".lower()
    for c in camps:
        for u in (c.get("source_urls") or "").split():
            u = u.strip().lower().rstrip("/")
            if not u:
                continue
            tail = u.split("/")[-1].lstrip("@")
            if u in hay or (len(tail) >= 4 and tail in hay.replace(" ", "")):
                return c
        words = {w for w in re.findall(r"[a-z0-9]+", (c.get("name") or "").lower()) if len(w) >= 3 and w not in STOP}
        if words and any(re.search(rf"\b{re.escape(w)}\b", hay) for w in words):
            return c
    return None


def view(db: DB, data: dict | None) -> dict:
    """Decorate cached cards for the page: short numbers, ages, campaign + permission badges."""
    from .review import permitted_streamers
    camps = db.q("SELECT id, name, source_urls, rate_per_1k FROM campaigns WHERE active=1")
    permitted = permitted_streamers(db)
    queued = {r["url"] for r in db.q("SELECT url FROM sources WHERE platform IN ('manual','trending')")}
    now = _now()
    sections = []
    for s in SECTIONS:
        rows = (data or {}).get("rows", {}).get(s["key"], {"now": [], "all": []})
        tabs = []
        for tab, label in (("now", "Right now"), ("all", "All-time")):
            cards = []
            for i, c in enumerate(rows.get(tab, [])):
                c = dict(c)
                c["rank"] = i + 1
                c["views_s"] = short_num(c.get("views"))
                c["vph_s"] = short_num(c.get("vph") or views_per_hour(c.get("views", 0), c.get("published"), now))
                c["age"] = age_text(c.get("published"), now)
                m = int((c.get("duration_s") or 0) // 60)
                c["length"] = f"{m // 60}:{m % 60:02d}:00" if m >= 60 else (f"{m}:{int((c.get('duration_s') or 0) % 60):02d}" if c.get("duration_s") else "")
                camp = campaign_match(c, camps)
                c["campaign"] = camp["name"] if camp else ""
                c["campaign_rate"] = camp.get("rate_per_1k") if camp else 0
                c["permitted"] = (c.get("channel") or "").lower().replace(" ", "") in {p.replace(" ", "") for p in permitted}
                c["queued"] = c["url"] in queued
                cards.append(c)
            if cards:
                tabs.append({"key": tab, "label": label, "cards": cards})
        sections.append({**s, "tabs": tabs})
    return {
        "sections": sections, "notes": (data or {}).get("notes", []),
        "updated": age_text((data or {}).get("fetched_at"), now) if data else "",
        "sources": (data or {}).get("sources", {}),
        "kick": settings.kick_streamers,
    }


def queue_clip(db: DB, url: str, title: str = "", streamer: str = "") -> int | None:
    """'Clip this': send a trending video to the Import queue."""
    from . import importer
    from .discovery import add_source
    if not re.match(r"^https://(www\.)?(youtube\.com|youtu\.be|clips\.twitch\.tv|(www\.)?twitch\.tv|kick\.com)/", url):
        raise ValueError("Only YouTube, Twitch and Kick links can be clipped from here")
    ext = re.sub(r"[^a-zA-Z0-9]+", "_", url)[-120:]
    sid = add_source(db, platform="manual", external_id=ext, url=url, streamer=streamer.strip()[:80],
                     title=title.strip()[:200] or url)
    if sid:
        layout = db.get_setting("import_layout", "auto")
        db.update("sources", sid, priority=10, progress="Waiting in line", **importer._options(layout, db.get_setting("import_clips")))
        importer.wake()
    return sid
