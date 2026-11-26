"""Streams: look up any streamer on Twitch, Kick or YouTube, see if they're live,
watch them, grab their hottest clips, and pick a past stream to scan.

Works without API keys (yt-dlp reads the public pages). With Twitch keys set it
uses the Twitch API instead, which is faster and gives live viewer counts.
"""
from __future__ import annotations

import json
import logging
import re
import shutil
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from urllib.parse import quote

import httpx

from .config import settings
from .db import DB

log = logging.getLogger("sxm.streams")
PLATFORMS = ("twitch", "kick", "youtube")
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0 Safari/537.36"
_cache: dict[str, tuple[float, dict]] = {}
_lock = threading.Lock()
CACHE_S = 120


# ---------------- input ----------------

def parse_query(q: str, platform: str = "twitch") -> dict:
    """'lacy', '@lacy', a channel link or a video link -> what to open."""
    q = (q or "").strip()
    m = re.search(r"twitch\.tv/videos/(\d+)", q)
    if m:
        return {"kind": "video", "platform": "twitch", "url": f"https://www.twitch.tv/videos/{m.group(1)}"}
    m = re.search(r"(clips\.twitch\.tv/|twitch\.tv/\w+/clip/)([\w-]+)", q)
    if m:
        return {"kind": "video", "platform": "twitch", "url": q}
    m = re.search(r"twitch\.tv/([A-Za-z0-9_]{2,25})", q)
    if m:
        return {"kind": "creator", "platform": "twitch", "handle": m.group(1).lower()}
    m = re.search(r"kick\.com/(?:video/|[\w-]+/videos/|[\w-]+/clips/)", q)
    if m:
        return {"kind": "video", "platform": "kick", "url": q}
    m = re.search(r"kick\.com/([\w-]{2,40})", q)
    if m:
        return {"kind": "creator", "platform": "kick", "handle": m.group(1).lower()}
    m = re.search(r"(youtube\.com/(watch\?v=|live/|shorts/)|youtu\.be/)([\w-]{6,})", q)
    if m:
        return {"kind": "video", "platform": "youtube", "url": f"https://www.youtube.com/watch?v={m.group(3)}"}
    m = re.search(r"youtube\.com/(@[\w.-]+|channel/[\w-]+|c/[\w.-]+)", q)
    if m:
        return {"kind": "creator", "platform": "youtube", "handle": m.group(1)}
    if re.match(r"https?://", q):
        return {"kind": "video", "platform": "link", "url": q}
    handle = q.lstrip("@").strip()
    if platform == "youtube":
        handle = "@" + handle.replace(" ", "")
    elif platform in ("twitch", "kick"):
        handle = handle.replace(" ", "").lower()
    return {"kind": "creator", "platform": platform if platform in PLATFORMS else "twitch", "handle": handle}


def channel_url(platform: str, handle: str) -> str:
    if platform == "kick":
        return f"https://kick.com/{handle}"
    if platform == "youtube":
        return f"https://www.youtube.com/{handle}" if handle.startswith(("@", "channel/", "c/")) else \
            f"https://www.youtube.com/@{handle}"
    return f"https://www.twitch.tv/{handle}"


def embed_url(platform: str, handle: str, host: str, video_id: str = "") -> str:
    """A player for the live stream that works inside the app."""
    parent = (host or "localhost").split(":")[0]
    if platform == "twitch":
        return f"https://player.twitch.tv/?channel={quote(handle)}&parent={quote(parent)}&muted=true&autoplay=true"
    if platform == "kick":
        return f"https://player.kick.com/{quote(handle)}?muted=true&autoplay=true"
    if platform == "youtube" and video_id:
        return f"https://www.youtube.com/embed/{quote(video_id)}?autoplay=1&mute=1"
    return ""


# ---------------- yt-dlp ----------------

def _ytdlp_cmd() -> list[str]:
    exe = shutil.which("yt-dlp")
    return [exe] if exe else [sys.executable, "-m", "yt_dlp"]


def ytdlp_json(url: str, flat: bool = False, limit: int = 0, timeout: int = 45) -> dict | None:
    cmd = _ytdlp_cmd() + ["-J", "--no-warnings", "--skip-download"]
    if flat:
        cmd += ["--flat-playlist"]
    if limit:
        cmd += ["--playlist-end", str(limit)]
    try:
        p = subprocess.run(cmd + [url], capture_output=True, text=True, timeout=timeout,
                           encoding="utf-8", errors="replace")
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError) as e:
        log.info("yt-dlp %s: %s", url, e)
        return None
    if p.returncode != 0 or not p.stdout.strip():
        log.info("yt-dlp %s failed: %s", url, p.stderr[-300:])
        return None
    try:
        return json.loads(p.stdout)
    except ValueError:
        return None


def _thumb(e: dict) -> str:
    if e.get("thumbnail"):
        return e["thumbnail"]
    th = e.get("thumbnails") or []
    return (th[-1] or {}).get("url", "") if th else ""


def _when(ts) -> str:
    try:
        return datetime.fromtimestamp(float(ts), timezone.utc).isoformat()
    except (TypeError, ValueError, OSError):
        return ""


def _item(e: dict, kind: str) -> dict:
    url = e.get("webpage_url") or e.get("url") or ""
    return {"kind": kind, "id": str(e.get("id") or ""), "title": e.get("title") or e.get("fulltitle") or "",
            "url": url, "thumb": _thumb(e), "duration": e.get("duration") or 0,
            "views": e.get("view_count") or 0, "date": _when(e.get("timestamp") or e.get("release_timestamp"))}


# ---------------- per platform ----------------

def _twitch_helix(handle: str) -> dict | None:
    if not (settings.twitch_client_id and settings.twitch_client_secret):
        return None
    from .discovery import Twitch
    t = Twitch()
    h = t._auth()
    api = "https://api.twitch.tv/helix"
    with httpx.Client(timeout=20) as http:
        u = http.get(f"{api}/users", params={"login": handle}, headers=h).json().get("data") or []
        if not u:
            return {"found": False}
        u = u[0]
        live = (http.get(f"{api}/streams", params={"user_id": u["id"]}, headers=h).json().get("data") or [None])[0]
        vids = http.get(f"{api}/videos", params={"user_id": u["id"], "type": "archive", "first": 12},
                        headers=h).json().get("data") or []
        since = live["started_at"] if live else datetime.fromtimestamp(time.time() - 86400 * 2, timezone.utc).isoformat()
        clips = http.get(f"{api}/clips", params={"broadcaster_id": u["id"], "started_at": since, "first": 20},
                         headers=h).json().get("data") or []
    from .discovery import _parse_twitch_duration
    return {
        "found": True, "name": u["display_name"], "avatar": u.get("profile_image_url", ""),
        "live": {"title": live["title"], "viewers": live.get("viewer_count", 0), "game": live.get("game_name", ""),
                 "started": live["started_at"], "url": channel_url("twitch", handle),
                 "thumb": live.get("thumbnail_url", "").replace("{width}", "640").replace("{height}", "360")}
        if live else None,
        "vods": [{"kind": "vod", "id": v["id"], "title": v["title"], "url": v["url"],
                  "thumb": (v.get("thumbnail_url") or "").replace("%{width}", "640").replace("%{height}", "360"),
                  "duration": _parse_twitch_duration(v.get("duration", "")), "views": v.get("view_count", 0),
                  "date": v.get("created_at", "")} for v in vids],
        "clips": sorted([{"kind": "clip", "id": c["id"], "title": c["title"], "url": c["url"],
                          "thumb": c.get("thumbnail_url", ""), "duration": c.get("duration", 0),
                          "views": c.get("view_count", 0), "date": c.get("created_at", "")} for c in clips],
                        key=lambda c: -c["views"]),
    }


def _twitch(handle: str) -> dict:
    try:
        r = _twitch_helix(handle)
        if r is not None:
            return r
    except Exception as e:
        log.info("twitch api: %s", e)
    base = channel_url("twitch", handle)
    with ThreadPoolExecutor(3) as ex:
        f_live = ex.submit(ytdlp_json, base)
        f_vods = ex.submit(ytdlp_json, f"{base}/videos?filter=archives&sort=time", True, 12)
        f_clips = ex.submit(ytdlp_json, f"{base}/clips?filter=clips&range=24hr", True, 20)
        live, vods, clips = f_live.result(), f_vods.result(), f_clips.result()
    live_ok = bool(live and live.get("is_live"))
    return {
        "found": bool(live or (vods and vods.get("entries")) or (clips and clips.get("entries"))),
        "name": (live or {}).get("uploader") or handle, "avatar": "",
        "live": {"title": live.get("description") or live.get("title") or "", "viewers": live.get("concurrent_view_count") or 0,
                 "game": "", "started": _when(live.get("timestamp")), "url": base, "thumb": _thumb(live)}
        if live_ok else None,
        "vods": [_item(e, "vod") for e in (vods or {}).get("entries") or [] if e],
        "clips": sorted([_item(e, "clip") for e in (clips or {}).get("entries") or [] if e], key=lambda c: -c["views"]),
    }


def _kick(handle: str) -> dict:
    out = {"found": False, "name": handle, "avatar": "", "live": None, "vods": [], "clips": []}
    hdr = {"User-Agent": UA, "Accept": "application/json"}
    try:
        with httpx.Client(timeout=15, headers=hdr, follow_redirects=True) as http:
            r = http.get(f"https://kick.com/api/v2/channels/{handle}")
            if r.status_code == 200:
                d = r.json()
                u = d.get("user") or {}
                ls = d.get("livestream")
                out.update(found=True, name=u.get("username") or handle, avatar=u.get("profile_pic") or "")
                if ls:
                    out["live"] = {"title": ls.get("session_title", ""), "viewers": ls.get("viewer_count", 0),
                                   "game": ((ls.get("categories") or [{}])[0] or {}).get("name", ""),
                                   "started": ls.get("created_at", ""), "url": channel_url("kick", handle),
                                   "thumb": (ls.get("thumbnail") or {}).get("url", "")}
                for v in (d.get("previous_livestreams") or [])[:12]:
                    vid = (v.get("video") or {}).get("uuid")
                    if vid:
                        out["vods"].append({"kind": "vod", "id": vid, "title": v.get("session_title", ""),
                                            "url": f"https://kick.com/{handle}/videos/{vid}",
                                            "thumb": (v.get("thumbnail") or {}).get("src", ""),
                                            "duration": (v.get("duration") or 0) / 1000, "views": v.get("views", 0),
                                            "date": v.get("created_at", "")})
            c = http.get(f"https://kick.com/api/v2/channels/{handle}/clips", params={"sort": "view", "time": "day"})
            if c.status_code == 200:
                for x in (c.json().get("clips") or [])[:20]:
                    out["clips"].append({"kind": "clip", "id": x.get("id", ""), "title": x.get("title", ""),
                                         "url": f"https://kick.com/{handle}/clips/{x.get('id', '')}",
                                         "thumb": x.get("thumbnail_url", ""), "duration": x.get("duration", 0),
                                         "views": x.get("views") or x.get("view_count") or 0,
                                         "date": x.get("created_at", "")})
    except Exception as e:
        log.info("kick api %s: %s", handle, e)
    if not out["found"]:  # Kick's site may block plain requests; yt-dlp can still tell if they're live
        live = ytdlp_json(channel_url("kick", handle))
        if live:
            out.update(found=True, name=live.get("uploader") or handle)
            if live.get("is_live"):
                out["live"] = {"title": live.get("title", ""), "viewers": live.get("concurrent_view_count") or 0,
                               "game": "", "started": _when(live.get("timestamp")),
                               "url": channel_url("kick", handle), "thumb": _thumb(live)}
    return out


def _youtube(handle: str) -> dict:
    base = channel_url("youtube", handle)
    with ThreadPoolExecutor(3) as ex:
        f_live = ex.submit(ytdlp_json, f"{base}/live")
        f_vods = ex.submit(ytdlp_json, f"{base}/streams", True, 12)
        f_short = ex.submit(ytdlp_json, f"{base}/videos", True, 12)
        live, vods, vids = f_live.result(), f_vods.result(), f_short.result()
    live_ok = bool(live and live.get("is_live"))
    name = (live or vods or vids or {}).get("channel") or (vods or vids or {}).get("uploader") or handle
    items = [_item(e, "vod") for e in (vods or {}).get("entries") or [] if e and e.get("live_status") != "is_live"]
    for it in items:
        if it["url"] and not it["url"].startswith("http"):
            it["url"] = f"https://www.youtube.com/watch?v={it['id']}"
    return {
        "found": bool(live or vods or vids), "name": name, "avatar": "",
        "live": {"title": live.get("title", ""), "viewers": live.get("concurrent_view_count") or 0, "game": "",
                 "started": _when(live.get("release_timestamp") or live.get("timestamp")),
                 "url": live.get("webpage_url") or base, "thumb": _thumb(live), "video_id": live.get("id", "")}
        if live_ok else None,
        "vods": items,
        "clips": [dict(_item(e, "clip"), url=f"https://www.youtube.com/watch?v={e.get('id')}")
                  for e in (vids or {}).get("entries") or [] if e][:12],
    }


def creator(platform: str, handle: str, fresh: bool = False) -> dict:
    """Everything the Streams page shows for one streamer (cached for 2 minutes)."""
    platform = platform if platform in PLATFORMS else "twitch"
    key = f"{platform}:{handle.lower()}"
    with _lock:
        hit = _cache.get(key)
    if hit and not fresh and time.time() - hit[0] < CACHE_S:
        return hit[1]
    fn = {"twitch": _twitch, "kick": _kick, "youtube": _youtube}[platform]
    try:
        data = fn(handle)
    except Exception as e:
        log.warning("streams %s: %s", key, e)
        data = {"found": False, "name": handle, "avatar": "", "live": None, "vods": [], "clips": [], "error": str(e)}
    data.update(platform=platform, handle=handle, channel_url=channel_url(platform, handle), checked=time.time())
    with _lock:
        _cache[key] = (time.time(), data)
    return data


# ---------------- favourites ----------------

def favorites(db: DB) -> list[dict]:
    try:
        favs = json.loads(db.get_setting("stream_favorites", "[]") or "[]")
    except ValueError:
        favs = []
    if not favs:  # start with the streamers you already clip
        seen = set()
        for plat, names in (("twitch", settings.twitch_streamers), ("kick", settings.kick_streamers),
                            ("twitch", [x for x in (db.get_setting("permitted_streamers", "") or "").split(",")])):
            for n in names:
                n = n.strip().lower()
                if n and n not in seen:
                    seen.add(n)
                    favs.append({"platform": plat, "handle": n, "name": n, "avatar": ""})
    return favs


def toggle_favorite(db: DB, platform: str, handle: str, name: str = "", avatar: str = "") -> bool:
    favs = favorites(db)
    key = (platform, handle.lower())
    keep = [f for f in favs if (f["platform"], f["handle"].lower()) != key]
    added = len(keep) == len(favs)
    if added:
        keep.insert(0, {"platform": platform, "handle": handle.lower(), "name": name or handle, "avatar": avatar})
    db.set_setting("stream_favorites", json.dumps(keep[:40]))
    return added


def is_favorite(db: DB, platform: str, handle: str) -> bool:
    return any((f["platform"], f["handle"].lower()) == (platform, handle.lower()) for f in favorites(db))


def live_board(db: DB, max_n: int = 12) -> list[dict]:
    """Which favourites are live right now (checked in parallel, cached)."""
    favs = favorites(db)[:max_n]
    with ThreadPoolExecutor(6) as ex:
        rows = list(ex.map(lambda f: creator(f["platform"], f["handle"]), favs))
    for f, r in zip(favs, rows):
        f["live"] = r.get("live")
        f["avatar"] = f.get("avatar") or r.get("avatar", "")
    return favs


# ---------------- scan ----------------

SCAN_STEPS = [("fetch", "Fetch stream", "Grabbing the stream (just the audio for long ones)"),
              ("transcribe", "Transcribe audio", "Turning every word into timed text"),
              ("find", "Find the moments", "Hype, reactions and punchlines"),
              ("cut", "Cut the clips", "Fetching only those parts and editing them")]


def queue_scan(db: DB, url: str, *, title: str = "", streamer: str = "", platform: str = "manual",
               clips: int = 6, window: str = "any", from_min: float = 0, to_min: float = 0,
               duration: float = 0, prompt: str = "", layout: str = "auto", min_s=None, max_s=None) -> int:
    """Start a scan of a stream / VOD / clip link. Returns the source id."""
    from . import importer
    from .discovery import add_source
    if not re.match(r"^https?://", url or ""):
        raise ValueError("Paste a stream or video link")
    a = b = 0.0
    if window == "custom":
        a, b = max(0.0, float(from_min or 0) * 60), max(0.0, float(to_min or 0) * 60)
        if b and b <= a:
            raise ValueError("The end time has to be after the start time")
    part = window if window in ("begin", "middle", "end") else None
    ext = re.sub(r"[^a-zA-Z0-9]+", "_", url)[-100:] + f"_{int(time.time())}"
    sid = add_source(db, platform="manual", external_id=ext, url=url, streamer=(streamer or "").strip()[:80],
                     title=(title or url).strip()[:200], duration_s=duration or None)
    opts = importer._options(layout or "auto", clips, min_s, max_s)
    if int(clips or 0) > 15:  # "Unlimited": every strong moment it finds
        opts["clips_wanted"] = min(int(clips), 30)
    db.update("sources", sid, priority=20, progress="Waiting in line", scan_from=a or None, scan_to=b or None,
              scan_part=part, find_prompt=(prompt or "").strip()[:300] or None, **opts)
    importer.wake()
    return sid


def scan_status(db: DB, sid: int) -> dict | None:
    s = db.one("SELECT * FROM sources WHERE id=?", (sid,))
    if not s:
        return None
    prog = (s.get("progress") or "").lower()
    st = s["status"]
    idx = 0
    if st in ("clipping",):
        idx = 1
        if any(k in prog for k in ("finding", "measuring", "detecting", "writing", "hooks")):
            idx = 2
        if "cutting clip" in prog or "editing clip" in prog:
            idx = 3
    elif st == "clipped":
        idx = 4
    m = re.search(r"(\d+)%", prog)
    pct = int(m.group(1)) if m else None
    m2 = re.search(r"clip (\d+) of (\d+)", prog)
    if idx == 3 and m2:
        pct = int((int(m2.group(1)) - 1) / int(m2.group(2)) * 100)
    overall = min(100, int(idx * 25 + (pct or 0) / 4)) if st != "clipped" else 100
    clips = db.q("SELECT id, hook, status, score, thumb_path, end_s - start_s secs FROM clips WHERE source_id=? "
                 "AND status != 'rendering' ORDER BY score DESC", (sid,))
    ahead = db.one("SELECT COUNT(*) n FROM sources WHERE priority>0 AND status IN ('new','downloaded') AND "
                   "(priority > ? OR (priority = ? AND id < ?))", (s.get("priority") or 0, s.get("priority") or 0, sid))
    return {"id": sid, "status": st, "progress": s.get("progress") or "", "error": s.get("error") or "",
            "step": idx, "pct": pct, "overall": overall, "title": s.get("title") or s["url"],
            "streamer": s.get("streamer") or "", "url": s["url"], "prompt": s.get("find_prompt") or "",
            "waiting": st in ("new", "downloaded"), "ahead": (ahead or {}).get("n", 0),
            "clips": clips, "steps": [{"key": k, "title": t, "sub": d} for k, t, d in SCAN_STEPS]}


# ---------------- most viral right now ----------------

VIRAL_S = 180  # cache the board for 3 minutes
YT_LIVE_SEARCH = "https://www.youtube.com/results?search_query={q}&sp=EgJAAQ%253D%253D"  # filter: Live


def _g(d, *path, default=""):
    for p in path:
        d = d.get(p) if isinstance(d, dict) else None
        if d is None:
            return default
    return d


def _pic(*vals) -> str:
    for v in vals:
        if isinstance(v, str) and v.startswith("http"):
            return v
        if isinstance(v, dict):
            for k in ("src", "url", "responsive"):
                if isinstance(v.get(k), str) and v[k].startswith("http"):
                    return v[k]
    return ""


def _kick_streams(http: httpx.Client, n: int) -> list[dict]:
    rows = []
    try:  # Kick's site API: live streams sorted by viewers
        r = http.get("https://web.kick.com/api/v1/livestreams", params={"limit": min(n, 24), "sort": "viewer_count_desc"})
        if r.status_code == 200:
            for s in _g(r.json(), "data", "livestreams", default=[]) or []:
                ch = s.get("channel") or {}
                slug = ch.get("slug") or ch.get("username") or ""
                rows.append({"platform": "kick", "handle": slug, "name": ch.get("username") or slug,
                             "avatar": _pic(ch.get("profile_picture"), ch.get("profile_pic"), ch.get("avatar")),
                             "title": s.get("title") or "", "viewers": s.get("viewer_count") or 0,
                             "game": _g(s, "category", "name"), "started": s.get("start_time") or "",
                             "thumb": _pic(s.get("thumbnail")), "url": channel_url("kick", slug)})
    except Exception as e:
        log.info("kick web livestreams: %s", e)
    if not rows:
        try:
            r = http.get("https://kick.com/stream/livestreams/en", params={"page": 1, "limit": min(n, 24), "sort": "desc"})
            if r.status_code == 200:
                for s in r.json().get("data") or []:
                    ch = s.get("channel") or {}
                    slug = ch.get("slug") or ""
                    rows.append({"platform": "kick", "handle": slug, "name": _g(ch, "user", "username") or slug,
                                 "avatar": _pic(_g(ch, "user", "profile_pic")), "title": s.get("session_title") or "",
                                 "viewers": s.get("viewer_count") or s.get("viewers") or 0,
                                 "game": ((s.get("categories") or [{}])[0] or {}).get("name", ""),
                                 "started": s.get("start_time") or s.get("created_at") or "",
                                 "thumb": _pic(s.get("thumbnail")), "url": channel_url("kick", slug)})
        except Exception as e:
            log.info("kick livestreams: %s", e)
    return [r for r in rows if r["handle"]]


def _kick_clips(http: httpx.Client, n: int) -> list[dict]:
    try:
        r = http.get("https://kick.com/api/v2/clips", params={"sort": "view", "time": "day"})
        if r.status_code != 200:
            return []
        out = []
        for c in (r.json().get("clips") or [])[:n]:
            ch = c.get("channel") or {}
            slug = ch.get("slug") or ch.get("username") or ""
            out.append({"platform": "kick", "kind": "clip", "id": c.get("id", ""), "title": c.get("title") or "",
                        "url": f"https://kick.com/{slug}/clips/{c.get('id', '')}", "thumb": c.get("thumbnail_url") or "",
                        "duration": c.get("duration") or 0, "views": c.get("views") or c.get("view_count") or 0,
                        "date": c.get("created_at") or "", "handle": slug, "name": ch.get("username") or slug,
                        "game": _g(c, "category", "name")})
        return out
    except Exception as e:
        log.info("kick clips: %s", e)
        return []


def _twitch_viral(n: int) -> tuple[list[dict], list[dict], str]:
    """Top live streams and their hottest clips today. Needs your free Twitch app keys."""
    if not (settings.twitch_client_id and settings.twitch_client_secret):
        return [], [], "keys"
    from .discovery import Twitch
    tw = Twitch(httpx.Client(timeout=20))
    live = tw.get("streams", first=min(n, 50))
    users = {}
    ids = [s["user_id"] for s in live]
    if ids:
        r = tw.http.get("https://api.twitch.tv/helix/users", params=[("id", i) for i in ids], headers=tw._auth())
        users = {u["id"]: u for u in r.json().get("data", [])}
    rows = [{"platform": "twitch", "handle": s["user_login"], "name": s["user_name"],
             "avatar": users.get(s["user_id"], {}).get("profile_image_url", ""), "title": s.get("title", ""),
             "viewers": s.get("viewer_count", 0), "game": s.get("game_name", ""), "started": s.get("started_at", ""),
             "thumb": (s.get("thumbnail_url") or "").replace("{width}", "640").replace("{height}", "360"),
             "url": channel_url("twitch", s["user_login"])} for s in live]
    since = datetime.fromtimestamp(time.time() - 86400, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    def top_clips(s):
        try:
            return tw.get("clips", broadcaster_id=s["user_id"], started_at=since, first=3)
        except Exception:
            return []
    clips = []
    with ThreadPoolExecutor(6) as ex:
        for s, cs in zip(live[:12], ex.map(top_clips, live[:12])):
            clips += [{"platform": "twitch", "kind": "clip", "id": c["id"], "title": c["title"], "url": c["url"],
                       "thumb": c.get("thumbnail_url", ""), "duration": c.get("duration", 0),
                       "views": c.get("view_count", 0), "date": c.get("created_at", ""), "handle": s["user_login"],
                       "name": s["user_name"], "game": s.get("game_name", "")} for c in cs]
    return rows, sorted(clips, key=lambda c: -c["views"])[:n], ""


def _youtube_viral(n: int, q: str = "live") -> list[dict]:
    d = ytdlp_json(YT_LIVE_SEARCH.format(q=quote(q or "live")), flat=True, limit=max(n, 30), timeout=60) or {}
    rows = []
    for e in d.get("entries") or []:
        if not e or e.get("live_status") != "is_live":
            continue
        handle = (e.get("uploader_id") or "").lstrip("@") or e.get("channel_id") or ""
        rows.append({"platform": "youtube", "handle": handle, "name": e.get("channel") or e.get("uploader") or handle,
                     "avatar": "", "title": e.get("title") or "", "viewers": e.get("concurrent_view_count") or 0,
                     "game": "", "started": _when(e.get("release_timestamp") or e.get("timestamp")),
                     "thumb": _thumb(e) or f"https://i.ytimg.com/vi/{e.get('id')}/hqdefault_live.jpg",
                     "url": f"https://www.youtube.com/watch?v={e.get('id')}", "video_id": e.get("id", "")})
    return sorted(rows, key=lambda r: -r["viewers"])


def viral(platform: str = "all", fresh: bool = False, n: int = 24) -> dict:
    """The biggest live streams right now (ranked by viewers) and the most watched clips today."""
    key = f"viral:{platform}"
    with _lock:
        hit = _cache.get(key)
    if hit and not fresh and time.time() - hit[0] < VIRAL_S:
        return hit[1]
    want = PLATFORMS if platform == "all" else (platform,)
    out = {"streams": [], "clips": [], "notes": {}, "platform": platform}
    hdr = {"User-Agent": UA, "Accept": "application/json"}

    def run(p):
        try:
            if p == "kick":
                with httpx.Client(timeout=20, headers=hdr, follow_redirects=True) as http:
                    return p, _kick_streams(http, n), _kick_clips(http, n), ""
            if p == "twitch":
                s, c, note = _twitch_viral(n)
                return p, s, c, note
            return p, _youtube_viral(n), [], ""
        except Exception as e:
            log.warning("viral %s: %s", p, e)
            return p, [], [], "error"
    with ThreadPoolExecutor(3) as ex:
        for p, s, c, note in ex.map(run, want):
            out["streams"] += s
            out["clips"] += c
            if note or not s:
                out["notes"][p] = note or "empty"
    out["streams"].sort(key=lambda r: -(r["viewers"] or 0))
    out["clips"].sort(key=lambda r: -(r["views"] or 0))
    out["checked"] = time.time()
    with _lock:
        _cache[key] = (time.time(), out)
    return out
