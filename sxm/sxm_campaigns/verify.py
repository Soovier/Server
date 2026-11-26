"""Account ownership and view verification.

YouTube: fully automatic with a free API key. Ownership = the code appears in
  the channel description; views = the Data API's public viewCount, and the
  video must belong to the clipper's verified channel.
Instagram: ownership via Meta's Business Discovery (the account must be a
  Business/Creator account with the code in its bio). Meta doesn't show other
  people's view counts, so views are reported by the clipper with a screenshot
  (or sent by SXM from the clipper's own insights) and the brand
  confirms them before they pay.
Facebook / TikTok: reported views + screenshot, admin verifies ownership.
"""
from __future__ import annotations

import re
import secrets
from urllib.parse import parse_qs, urlparse

import httpx

from .config import settings
from .db import DB, utcnow

YT = "https://www.googleapis.com/youtube/v3"
PLATFORMS = ("youtube", "instagram", "facebook", "tiktok")


class VerifyError(ValueError):
    pass


def new_code() -> str:
    return "SXM-" + secrets.token_hex(3).upper()


def platform_of(url: str) -> str | None:
    host = (urlparse(url).hostname or "").lower()
    if host.endswith("youtube.com") or host == "youtu.be":
        return "youtube"
    if host.endswith("instagram.com"):
        return "instagram"
    if host.endswith("facebook.com") or host == "fb.watch":
        return "facebook"
    if host.endswith("tiktok.com"):
        return "tiktok"
    return None


def youtube_id(url: str) -> str | None:
    u = urlparse(url)
    host = (u.hostname or "").lower()
    if host == "youtu.be":
        return u.path.strip("/").split("/")[0] or None
    m = re.match(r"^/(shorts|live|embed)/([A-Za-z0-9_-]{6,})", u.path)
    if m:
        return m.group(2)
    v = parse_qs(u.query).get("v")
    return v[0] if v else None


def post_id(platform: str, url: str) -> str:
    if platform == "youtube":
        vid = youtube_id(url)
        if not vid:
            raise VerifyError("That doesn't look like a YouTube video link")
        return vid
    m = re.search(r"/(reel|reels|p|videos|video)/([A-Za-z0-9_.-]+)", urlparse(url).path)
    return m.group(2) if m else url


# ---------------- YouTube ----------------

def _yt(http: httpx.Client, path: str, **params) -> dict:
    if not settings.youtube_api_key:
        raise VerifyError("YOUTUBE_API_KEY is not set")
    r = http.get(f"{YT}/{path}", params={**params, "key": settings.youtube_api_key})
    if r.status_code >= 400:
        raise VerifyError(f"YouTube API {r.status_code}")
    return r.json()


def youtube_channel(http: httpx.Client, handle_or_url: str) -> dict | None:
    h = handle_or_url.strip()
    m = re.search(r"/channel/(UC[\w-]+)", h)
    if m or h.startswith("UC"):
        params = {"id": m.group(1) if m else h}
    else:
        h = re.sub(r"^https?://(www\.)?youtube\.com/", "", h).strip("/")
        params = {"forHandle": h if h.startswith("@") else "@" + h}
    items = _yt(http, "channels", part="snippet", **params).get("items", [])
    return items[0] if items else None


def youtube_video(http: httpx.Client, video_id: str) -> dict | None:
    items = _yt(http, "videos", part="snippet,statistics", id=video_id).get("items", [])
    return items[0] if items else None


# ---------------- Instagram (Business Discovery) ----------------

def instagram_bio(http: httpx.Client, username: str) -> str | None:
    if not (settings.meta_ig_user_id and settings.meta_access_token):
        return None
    r = http.get(f"https://graph.facebook.com/{settings.meta_graph_version}/{settings.meta_ig_user_id}", params={
        "fields": f"business_discovery.username({username.lstrip('@')}){{biography}}",
        "access_token": settings.meta_access_token})
    if r.status_code >= 400:
        return None
    return (r.json().get("business_discovery") or {}).get("biography", "")


# ---------------- ownership ----------------

def add_account(db: DB, user_id: int, platform: str, handle: str) -> dict:
    if platform not in PLATFORMS:
        raise VerifyError("Unsupported platform")
    handle = handle.strip()
    if not handle:
        raise VerifyError("Enter your handle or channel link")
    existing = db.one("SELECT * FROM social_accounts WHERE platform=? AND handle=?", (platform, handle))
    if existing:
        if existing["user_id"] != user_id:
            raise VerifyError("That account is already claimed")
        return existing
    sid = db.insert("social_accounts", {"user_id": user_id, "platform": platform, "handle": handle,
                                        "code": new_code(), "created_at": utcnow()})
    return db.one("SELECT * FROM social_accounts WHERE id=?", (sid,))


def check_account(db: DB, account_id: int, http: httpx.Client | None = None) -> bool:
    """Look for the code in the channel description / bio."""
    acc = db.one("SELECT * FROM social_accounts WHERE id=?", (account_id,))
    http = http or httpx.Client(timeout=20)
    if acc["platform"] == "youtube":
        ch = youtube_channel(http, acc["handle"])
        if ch and acc["code"] in (ch["snippet"].get("description") or ""):
            db.update("social_accounts", account_id, verified=1, verified_by="api", external_id=ch["id"])
            return True
        return False
    if acc["platform"] == "instagram":
        bio = instagram_bio(http, acc["handle"])
        if bio and acc["code"] in bio:
            db.update("social_accounts", account_id, verified=1, verified_by="api")
            return True
    return False


# ---------------- views ----------------

def check_views(db: DB, submission: dict, http: httpx.Client | None = None) -> int | None:
    """Return API-verified views for a post, or None when the platform has no public count."""
    if submission["platform"] != "youtube" or not settings.youtube_api_key:
        return None
    http = http or httpx.Client(timeout=20)
    v = youtube_video(http, submission["external_id"])
    if not v:
        raise VerifyError("Video not found or private")
    owned = {a["external_id"] for a in db.q(
        "SELECT external_id FROM social_accounts WHERE user_id=? AND platform='youtube' AND verified=1",
        (submission["clipper_id"],))}
    if v["snippet"]["channelId"] not in owned:
        raise VerifyError("This video isn't on a YouTube channel you've verified")
    return int(v["statistics"].get("viewCount", 0))
