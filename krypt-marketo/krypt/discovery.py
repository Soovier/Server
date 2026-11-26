"""Find long-form source videos to clip.

Sources, in priority order:
  1. Content Rewards campaign sources (the footage a campaign names; this is
     the content you have permission to clip and get paid for).
  2. Twitch VODs and top clips for streamers you list (official Helix API).
  3. YouTube uploads from channels you list (official Data API).
  4. Kick / anything else: paste a URL on the Sources page (yt-dlp handles it).

Clipping a streamer's content and posting it for money needs their permission
(a campaign is that permission). Sources not tied to a campaign are marked
`needs_permission` and are never auto-approved.
"""
from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx

from .config import settings
from .db import DB, utcnow

TWITCH_API = "https://api.twitch.tv/helix"
YT_API = "https://www.googleapis.com/youtube/v3"


def _parse_twitch_duration(s: str) -> float:
    # "3h2m10s"
    total = 0.0
    for num, unit in re.findall(r"(\d+)([hms])", s or ""):
        total += int(num) * {"h": 3600, "m": 60, "s": 1}[unit]
    return total


def _parse_iso_duration(s: str) -> float:
    m = re.match(r"P(?:(\d+)D)?T?(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?", s or "")
    if not m:
        return 0.0
    d, h, mi, se = (int(x or 0) for x in m.groups())
    return d * 86400 + h * 3600 + mi * 60 + se


def add_source(db: DB, *, platform: str, external_id: str, url: str, streamer: str = "",
               title: str = "", duration_s: float | None = None, view_count: int | None = None,
               campaign_id: int | None = None) -> int | None:
    """Insert a source if new. Returns the new id, or None if already known."""
    before = db.one("SELECT id FROM sources WHERE platform=? AND external_id=?", (platform, external_id))
    if before:
        return None
    return db.insert("sources", {
        "platform": platform, "external_id": external_id, "url": url, "streamer": streamer,
        "title": title, "duration_s": duration_s, "view_count": view_count,
        "campaign_id": campaign_id, "found_at": utcnow(),
    })


def add_manual_url(db: DB, url: str, campaign_id: int | None = None) -> int | None:
    ext = re.sub(r"[^a-zA-Z0-9]+", "_", url)[-120:]
    return add_source(db, platform="campaign" if campaign_id else "manual", external_id=ext,
                      url=url, title=url, campaign_id=campaign_id)


# ---------------- Twitch ----------------

class Twitch:
    def __init__(self, client: httpx.Client | None = None):
        self.http = client or httpx.Client(timeout=30)
        self._token: str | None = None

    def _auth(self) -> dict[str, str]:
        if not self._token:
            r = self.http.post("https://id.twitch.tv/oauth2/token", params={
                "client_id": settings.twitch_client_id,
                "client_secret": settings.twitch_client_secret,
                "grant_type": "client_credentials",
            })
            r.raise_for_status()
            self._token = r.json()["access_token"]
        return {"Client-Id": settings.twitch_client_id, "Authorization": f"Bearer {self._token}"}

    def get(self, path: str, **params: Any) -> list[dict]:
        r = self.http.get(f"{TWITCH_API}/{path}", params=params, headers=self._auth())
        r.raise_for_status()
        return r.json().get("data", [])

    def user_ids(self, logins: list[str]) -> dict[str, str]:
        if not logins:
            return {}
        r = self.http.get(f"{TWITCH_API}/users", params=[("login", l) for l in logins], headers=self._auth())
        r.raise_for_status()
        return {u["login"].lower(): u["id"] for u in r.json().get("data", [])}

    def discover(self, db: DB, logins: list[str], days: int = 3) -> int:
        found = 0
        since = (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%SZ")
        for login, uid in self.user_ids(logins).items():
            # Top community clips: already-proven moments, short, great to reframe.
            for c in self.get("clips", broadcaster_id=uid, started_at=since, first=10):
                if add_source(db, platform="twitch_clip", external_id=c["id"], url=c["url"], streamer=login,
                              title=c["title"], duration_s=c.get("duration"), view_count=c.get("view_count")):
                    found += 1
            # Recent full VODs: highlight detection finds moments nobody clipped yet.
            for v in self.get("videos", user_id=uid, type="archive", first=2):
                if add_source(db, platform="twitch", external_id=v["id"], url=v["url"], streamer=login,
                              title=v["title"], duration_s=_parse_twitch_duration(v.get("duration", "")),
                              view_count=v.get("view_count")):
                    found += 1
        return found


# ---------------- YouTube (source channels) ----------------

def discover_youtube(db: DB, channel_ids: list[str], api_key_or_token: str, client: httpx.Client | None = None,
                     use_bearer: bool = True) -> int:
    http = client or httpx.Client(timeout=30)
    auth = {"headers": {"Authorization": f"Bearer {api_key_or_token}"}} if use_bearer else {}
    found = 0
    for ch in channel_ids:
        # Uploads playlist id is the channel id with UC -> UU
        playlist = "UU" + ch[2:] if ch.startswith("UC") else ch
        params = {"part": "contentDetails,snippet", "playlistId": playlist, "maxResults": 5}
        if not use_bearer:
            params["key"] = api_key_or_token
        r = http.get(f"{YT_API}/playlistItems", params=params, **auth)
        r.raise_for_status()
        ids = [i["contentDetails"]["videoId"] for i in r.json().get("items", [])]
        if not ids:
            continue
        vparams = {"part": "contentDetails,snippet,statistics", "id": ",".join(ids)}
        if not use_bearer:
            vparams["key"] = api_key_or_token
        vr = http.get(f"{YT_API}/videos", params=vparams, **auth)
        vr.raise_for_status()
        for v in vr.json().get("items", []):
            dur = _parse_iso_duration(v["contentDetails"]["duration"])
            if dur < 180:  # skip shorts, we want long-form sources
                continue
            if add_source(db, platform="youtube", external_id=v["id"],
                          url=f"https://www.youtube.com/watch?v={v['id']}",
                          streamer=v["snippet"]["channelTitle"], title=v["snippet"]["title"],
                          duration_s=dur, view_count=int(v["statistics"].get("viewCount", 0))):
                found += 1
    return found


# ---------------- Campaign sources ----------------

def discover_campaigns(db: DB) -> int:
    found = 0
    for camp in db.q("SELECT * FROM campaigns WHERE active=1"):
        for url in (camp["source_urls"] or "").splitlines():
            url = url.strip()
            if url and add_manual_url(db, url, campaign_id=camp["id"]):
                found += 1
    return found


def run_discovery(db: DB) -> str:
    notes = []
    from . import rewards_client
    if rewards_client.enabled():
        try:
            notes.append(f"krypt rewards campaigns new: {rewards_client.sync_campaigns(db)}")
        except Exception as e:
            notes.append(f"krypt rewards sync failed: {e}")
    n = discover_campaigns(db)
    notes.append(f"campaign sources: {n}")
    if settings.twitch_client_id and settings.twitch_streamers:
        try:
            n = Twitch().discover(db, settings.twitch_streamers)
            notes.append(f"twitch: {n}")
        except Exception as e:  # keep going with other sources
            notes.append(f"twitch failed: {e}")
    if settings.youtube_source_channels:
        acc = db.get_account("youtube")
        if acc and acc.get("access_token"):
            try:
                from .publishers.youtube import ensure_fresh_token
                token = ensure_fresh_token(db)
                n = discover_youtube(db, settings.youtube_source_channels, token)
                notes.append(f"youtube: {n}")
            except Exception as e:
                notes.append(f"youtube failed: {e}")
    return ", ".join(notes)
