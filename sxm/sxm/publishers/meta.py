"""Instagram Reels + Facebook Page Reels through the official Meta Graph API.

Both use Meta's resumable upload host (rupload.facebook.com), so videos are
sent straight from your computer; no public file hosting needed.
"""
from __future__ import annotations

import time
from pathlib import Path
from urllib.parse import urlencode

import httpx

from ..config import settings
from ..db import DB

SCOPES = [
    "pages_show_list", "pages_read_engagement", "pages_manage_posts", "read_insights",
    "business_management", "instagram_basic", "instagram_content_publish", "instagram_manage_insights",
]


def graph(path: str = "") -> str:
    return f"https://graph.facebook.com/{settings.meta_graph_version}/{path}".rstrip("/")


class MetaError(RuntimeError):
    pass


def _check(r: httpx.Response) -> dict:
    try:
        data = r.json()
    except ValueError:
        data = {}
    if r.status_code >= 400 or "error" in data:
        msg = (data.get("error") or {}).get("message") or r.text[:400]
        raise MetaError(f"Meta API {r.status_code}: {msg}")
    return data


# ---------------- OAuth ----------------

def login_url(state: str) -> str:
    return "https://www.facebook.com/{}/dialog/oauth?".format(settings.meta_graph_version) + urlencode({
        "client_id": settings.meta_app_id,
        "redirect_uri": f"{settings.base_url}/oauth/meta/callback",
        "state": state,
        "scope": ",".join(SCOPES),
        "response_type": "code",
    })


def finish_login(db: DB, code: str, http: httpx.Client | None = None) -> dict:
    """Exchange the code, then save every Facebook Page and linked Instagram
    account the login can reach. You pick which ones SXM posts to on Setup."""
    http = http or httpx.Client(timeout=30)
    short = _check(http.get(graph("oauth/access_token"), params={
        "client_id": settings.meta_app_id, "client_secret": settings.meta_app_secret,
        "redirect_uri": f"{settings.base_url}/oauth/meta/callback", "code": code,
    }))["access_token"]
    long_user = _check(http.get(graph("oauth/access_token"), params={
        "grant_type": "fb_exchange_token", "client_id": settings.meta_app_id,
        "client_secret": settings.meta_app_secret, "fb_exchange_token": short,
    }))["access_token"]
    pages: list[dict] = []
    url, params = graph("me/accounts"), {
        "access_token": long_user, "limit": 100,
        "fields": "id,name,access_token,picture{url},instagram_business_account{id,username,profile_picture_url}",
    }
    while url:  # follow paging so every Page shows up
        data = _check(http.get(url, params=params))
        pages += data.get("data", [])
        url, params = (data.get("paging") or {}).get("next"), None
    if not pages:
        raise MetaError("No Facebook Pages found. Make sure you ticked your Pages during login.")
    first_time = not db.channels("facebook") and not db.channels("instagram")
    found = {"facebook": [], "instagram": []}
    for pg in pages:
        # A Page token derived from a long-lived user token does not expire.
        db.save_channel("facebook", pg["id"], enabled=True if first_time and len(pages) == 1 else None,
                        name=pg["name"], avatar=((pg.get("picture") or {}).get("data") or {}).get("url", ""),
                        access_token=pg["access_token"])
        found["facebook"].append(pg["name"])
        ig = pg.get("instagram_business_account")
        if ig:
            db.save_channel("instagram", ig["id"], enabled=True if first_time and len(pages) == 1 else None,
                            name=ig.get("username", ""), handle="@" + ig.get("username", ""),
                            avatar=ig.get("profile_picture_url", ""), access_token=pg["access_token"],
                            extra={"page_id": pg["id"], "page_name": pg["name"]})
            found["instagram"].append(ig.get("username", ""))
    return {"pages": found["facebook"], "instagram": found["instagram"],
            "page": found["facebook"][0], "needs_choice": len(pages) > 1 or not first_time}


def _meta_channel(db: DB, platform: str, channel: dict | int | None) -> dict:
    if isinstance(channel, int):
        channel = db.get_channel(channel)
    channel = channel or db.get_account(platform)
    if not channel or not channel.get("access_token"):
        raise MetaError(f"{'Instagram' if platform == 'instagram' else 'Facebook Page'} not connected")
    return channel


def _rupload(http: httpx.Client, url: str, token: str, path: Path) -> dict:
    data = path.read_bytes()
    r = http.post(url, content=data, headers={
        "Authorization": f"OAuth {token}", "offset": "0", "file_size": str(len(data)),
        "Content-Type": "application/octet-stream",
    }, timeout=600)
    return _check(r)


def _caption(clip: dict) -> str:
    return " ".join(x for x in (clip.get("caption") or "", clip.get("hashtags") or "") if x).strip()[:2200]


# ---------------- Instagram ----------------

def publish_instagram(db: DB, clip: dict, channel: dict | None = None, http: httpx.Client | None = None,
                      poll_s: float = 5, max_wait_s: float = 600) -> dict:
    http = http or httpx.Client(timeout=60)
    acc = _meta_channel(db, "instagram", channel)
    token, ig = acc["access_token"], acc["account_id"]
    container = _check(http.post(graph(f"{ig}/media"), data={
        "media_type": "REELS", "upload_type": "resumable", "caption": _caption(clip),
        "share_to_feed": "true", "access_token": token,
    }))["id"]
    _rupload(http, f"https://rupload.facebook.com/ig-api-upload/{settings.meta_graph_version}/{container}",
             token, Path(clip["video_path"]))
    waited = 0.0
    while True:
        st = _check(http.get(graph(container), params={"fields": "status_code,status", "access_token": token}))
        code = st.get("status_code")
        if code == "FINISHED":
            break
        if code in ("ERROR", "EXPIRED") or waited > max_wait_s:
            raise MetaError(f"Instagram processing {code}: {st.get('status')}")
        time.sleep(poll_s)
        waited += poll_s
    media_id = _check(http.post(graph(f"{ig}/media_publish"),
                                data={"creation_id": container, "access_token": token}))["id"]
    info = _check(http.get(graph(media_id), params={"fields": "permalink,timestamp", "access_token": token}))
    return {"external_id": media_id, "permalink": info.get("permalink", "")}


# ---------------- Facebook Page Reels ----------------

def publish_facebook(db: DB, clip: dict, channel: dict | None = None, http: httpx.Client | None = None) -> dict:
    http = http or httpx.Client(timeout=60)
    acc = _meta_channel(db, "facebook", channel)
    token, page = acc["access_token"], acc["account_id"]
    start = _check(http.post(graph(f"{page}/video_reels"), data={"upload_phase": "start", "access_token": token}))
    video_id = start["video_id"]
    upload_url = start.get("upload_url") or \
        f"https://rupload.facebook.com/video-upload/{settings.meta_graph_version}/{video_id}"
    _rupload(http, upload_url, token, Path(clip["video_path"]))
    _check(http.post(graph(f"{page}/video_reels"), data={
        "upload_phase": "finish", "video_id": video_id, "video_state": "PUBLISHED",
        "description": _caption(clip), "access_token": token,
    }))
    return {"external_id": video_id, "permalink": f"https://www.facebook.com/reel/{video_id}"}


# ---------------- Analytics ----------------

def _insights(http: httpx.Client, path: str, metrics: list[str], token: str) -> dict[str, float]:
    """Ask for all metrics; if Meta rejects one, fall back to asking one by one."""
    def parse(data: dict) -> dict[str, float]:
        out = {}
        for item in data.get("data", []):
            vals = item.get("values") or [{}]
            v = item.get("total_value", {}).get("value", vals[-1].get("value", 0))
            out[item["name"]] = v if isinstance(v, (int, float)) else 0
        return out
    try:
        return parse(_check(http.get(graph(path), params={"metric": ",".join(metrics), "access_token": token})))
    except MetaError:
        out: dict[str, float] = {}
        for m in metrics:
            try:
                out.update(parse(_check(http.get(graph(path), params={"metric": m, "access_token": token}))))
            except MetaError:
                pass
        return out


def instagram_metrics(db: DB, media_id: str, http: httpx.Client | None = None, channel: dict | None = None) -> dict:
    http = http or httpx.Client(timeout=30)
    token = _meta_channel(db, "instagram", channel)["access_token"]
    m = _insights(http, f"{media_id}/insights", [
        "views", "reach", "likes", "comments", "shares", "saved",
        "ig_reels_avg_watch_time", "ig_reels_video_view_total_time",
    ], token)
    return {
        "views": int(m.get("views", 0)), "likes": int(m.get("likes", 0)), "comments": int(m.get("comments", 0)),
        "shares": int(m.get("shares", 0)),
        "watch_time_s": m.get("ig_reels_video_view_total_time", 0) / 1000.0,  # ms -> s
        "avg_view_s": m.get("ig_reels_avg_watch_time", 0) / 1000.0,
        "revenue_usd": 0.0, "raw": m,
    }


def facebook_metrics(db: DB, video_id: str, http: httpx.Client | None = None, channel: dict | None = None) -> dict:
    http = http or httpx.Client(timeout=30)
    token = _meta_channel(db, "facebook", channel)["access_token"]
    m = _insights(http, f"{video_id}/video_insights", [
        "blue_reels_play_count", "fb_reels_total_plays", "post_video_avg_time_watched", "post_video_view_time",
        "post_video_social_actions",
    ], token)
    social = m.get("post_video_social_actions", 0)
    return {
        "views": int(m.get("fb_reels_total_plays") or m.get("blue_reels_play_count", 0)),
        "likes": 0, "comments": 0, "shares": int(social) if isinstance(social, (int, float)) else 0,
        "watch_time_s": m.get("post_video_view_time", 0) / 1000.0,
        "avg_view_s": m.get("post_video_avg_time_watched", 0) / 1000.0,
        "revenue_usd": 0.0, "raw": m,
    }
