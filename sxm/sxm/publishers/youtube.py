"""YouTube Shorts upload (Data API v3) and analytics (YouTube Analytics API v2)."""
from __future__ import annotations

import json
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlencode

import httpx

from ..config import settings
from ..db import DB

SCOPES = [
    "https://www.googleapis.com/auth/youtube.upload",
    "https://www.googleapis.com/auth/youtube.readonly",
    "https://www.googleapis.com/auth/yt-analytics.readonly",
    "https://www.googleapis.com/auth/yt-analytics-monetary.readonly",
]
TOKEN_URL = "https://oauth2.googleapis.com/token"


class YouTubeError(RuntimeError):
    pass


def _check(r: httpx.Response) -> dict:
    try:
        data = r.json()
    except ValueError:
        data = {}
    if r.status_code >= 400:
        msg = (data.get("error") or {}).get("message") if isinstance(data.get("error"), dict) else data.get("error")
        raise YouTubeError(f"Google API {r.status_code}: {msg or r.text[:400]}")
    return data


def login_url(state: str) -> str:
    return "https://accounts.google.com/o/oauth2/v2/auth?" + urlencode({
        "client_id": settings.google_client_id,
        "redirect_uri": f"{settings.base_url}/oauth/google/callback",
        "response_type": "code", "scope": " ".join(SCOPES), "access_type": "offline",
        # select_account: Google shows the account + channel picker every time, so
        # each Connect can add a different channel (including Brand Account channels).
        "prompt": "select_account consent", "include_granted_scopes": "true", "state": state,
    })


def _expiry(seconds: int) -> str:
    return (datetime.now(timezone.utc) + timedelta(seconds=int(seconds) - 60)).replace(microsecond=0).isoformat()


def finish_login(db: DB, code: str, http: httpx.Client | None = None) -> dict:
    """Save every channel this Google login can post to.

    Google issues one login per channel: when an account owns several channels
    (Brand Accounts), its picker asks which one, and the token works for that
    channel. Connect again and pick another channel to add it too.
    """
    http = http or httpx.Client(timeout=30)
    tok = _check(http.post(TOKEN_URL, data={
        "code": code, "client_id": settings.google_client_id, "client_secret": settings.google_client_secret,
        "redirect_uri": f"{settings.base_url}/oauth/google/callback", "grant_type": "authorization_code",
    }))
    ch = _check(http.get("https://www.googleapis.com/youtube/v3/channels",
                         params={"part": "snippet,statistics", "mine": "true", "maxResults": 50},
                         headers={"Authorization": f"Bearer {tok['access_token']}"})).get("items", [])
    if not ch:
        raise YouTubeError("That Google account has no YouTube channel. Pick the channel itself on Google's screen.")
    saved = []
    for c in ch:
        sn, st = c.get("snippet", {}), c.get("statistics", {})
        existing = db.one("SELECT id FROM channels WHERE platform='youtube' AND account_id=?", (c["id"],))
        db.save_channel("youtube", c["id"], enabled=True if not existing else None,
                        name=sn.get("title", ""), handle=sn.get("customUrl", ""),
                        avatar=((sn.get("thumbnails") or {}).get("default") or {}).get("url", ""),
                        access_token=tok["access_token"],
                        **({"refresh_token": tok["refresh_token"]} if tok.get("refresh_token") else {}),
                        expires_at=_expiry(tok.get("expires_in", 3600)),
                        extra={"subscribers": int(st.get("subscriberCount", 0) or 0),
                               "videos": int(st.get("videoCount", 0) or 0)})
        saved.append(sn.get("title", c["id"]))
    return {"channels": saved, "channel": saved[0]}


def _yt_channel(db: DB, channel: dict | int | None) -> dict:
    if isinstance(channel, int):
        channel = db.get_channel(channel)
    channel = channel or db.get_account("youtube")
    if not channel:
        raise YouTubeError("YouTube not connected")
    return channel


def ensure_fresh_token(db: DB, channel: dict | int | None = None, http: httpx.Client | None = None) -> str:
    acc = _yt_channel(db, channel)
    acc = db.get_channel(acc["id"]) or acc
    if acc.get("expires_at") and acc["expires_at"] > datetime.now(timezone.utc).isoformat():
        return acc["access_token"]
    if not acc.get("refresh_token"):
        raise YouTubeError(f"YouTube login for {acc.get('name')} expired; reconnect it on the Setup page")
    http = http or httpx.Client(timeout=30)
    tok = _check(http.post(TOKEN_URL, data={
        "client_id": settings.google_client_id, "client_secret": settings.google_client_secret,
        "refresh_token": acc["refresh_token"], "grant_type": "refresh_token",
    }))
    db.update("channels", acc["id"], access_token=tok["access_token"],
              expires_at=_expiry(tok.get("expires_in", 3600)))
    return tok["access_token"]


def _title(clip: dict) -> str:
    base = (clip.get("hook") or clip.get("caption") or "Clip").strip()
    if "#shorts" not in base.lower():
        base = f"{base} #shorts"
    return base[:100]


def publish_youtube(db: DB, clip: dict, channel: dict | None = None, http: httpx.Client | None = None) -> dict:
    http = http or httpx.Client(timeout=60)
    token = ensure_fresh_token(db, channel, http)
    path = Path(clip["video_path"])
    size = path.stat().st_size
    tags = [t.lstrip("#") for t in (clip.get("hashtags") or "").split() if t.startswith("#")]
    meta = {
        "snippet": {"title": _title(clip),
                    "description": f"{clip.get('caption') or ''}\n\n{clip.get('hashtags') or ''}".strip()[:4900],
                    "tags": tags[:15], "categoryId": "20"},  # 20 = Gaming
        "status": {"privacyStatus": db.get_setting("youtube_privacy", "public"),
                   "selfDeclaredMadeForKids": False},
    }
    init = http.post("https://www.googleapis.com/upload/youtube/v3/videos?" + urlencode(
        {"uploadType": "resumable", "part": "snippet,status"}),
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json; charset=UTF-8",
                 "X-Upload-Content-Type": "video/mp4", "X-Upload-Content-Length": str(size)},
        content=json.dumps(meta))
    _check(init)
    upload_url = init.headers.get("Location")
    if not upload_url:
        raise YouTubeError("YouTube did not return an upload URL")
    done = _check(http.put(upload_url, content=path.read_bytes(),
                           headers={"Content-Type": "video/mp4", "Content-Length": str(size)}, timeout=900))
    vid = done["id"]
    return {"external_id": vid, "permalink": f"https://www.youtube.com/shorts/{vid}"}


def youtube_metrics(db: DB, video_ids: list[str], since: date, http: httpx.Client | None = None,
                    channel: dict | None = None) -> dict[str, dict]:
    """Per-video totals. Analytics lags ~2 days, so live view counts come from the
    Data API and watch time / revenue from the Analytics API."""
    if not video_ids:
        return {}
    http = http or httpx.Client(timeout=30)
    token = ensure_fresh_token(db, channel, http)
    h = {"Authorization": f"Bearer {token}"}
    out: dict[str, dict] = {v: {"views": 0, "likes": 0, "comments": 0, "shares": 0, "watch_time_s": 0.0,
                                "avg_view_s": 0.0, "revenue_usd": 0.0, "raw": {}} for v in video_ids}
    for i in range(0, len(video_ids), 50):
        batch = video_ids[i:i + 50]
        data = _check(http.get("https://www.googleapis.com/youtube/v3/videos",
                               params={"part": "statistics", "id": ",".join(batch)}, headers=h))
        for it in data.get("items", []):
            s = it.get("statistics", {})
            out[it["id"]].update(views=int(s.get("viewCount", 0)), likes=int(s.get("likeCount", 0)),
                                 comments=int(s.get("commentCount", 0)))
    metrics_full = "views,estimatedMinutesWatched,averageViewDuration,shares,estimatedRevenue"
    for i in range(0, len(video_ids), 200):
        batch = video_ids[i:i + 200]
        params = {"ids": "channel==MINE", "startDate": since.isoformat(),
                  "endDate": date.today().isoformat(), "dimensions": "video",
                  "filters": "video==" + ",".join(batch), "maxResults": 200}
        try:
            rep = _check(http.get("https://youtubeanalytics.googleapis.com/v2/reports",
                                  params={**params, "metrics": metrics_full}, headers=h))
        except YouTubeError:
            # Channel not in the Partner Program -> revenue metric is refused.
            rep = _check(http.get("https://youtubeanalytics.googleapis.com/v2/reports",
                                  params={**params, "metrics": metrics_full.replace(",estimatedRevenue", "")},
                                  headers=h))
        cols = [c["name"] for c in rep.get("columnHeaders", [])]
        for row in rep.get("rows", []) or []:
            r = dict(zip(cols, row))
            v = out.get(r["video"])
            if v is None:
                continue
            v["views"] = max(v["views"], int(r.get("views", 0)))
            v["watch_time_s"] = float(r.get("estimatedMinutesWatched", 0)) * 60
            v["avg_view_s"] = float(r.get("averageViewDuration", 0))
            v["shares"] = int(r.get("shares", 0))
            v["revenue_usd"] = float(r.get("estimatedRevenue", 0) or 0)
            v["raw"] = r
    return out
