"""Campaign finder: paste a campaign link and/or describe it, and SXM finds the
videos to clip for it.

    1. Read the brief    fetch the campaign page; Claude pulls out the creator(s),
                         what to clip, rules, pay and links (works without Claude too)
    2. Find videos       the page's own links, the creators' recent long videos on
                         YouTube and Twitch, and YouTube searches for the content
    3. You choose        format, how many clips, which videos (top 10 picked for you)
    4. Make clips        a campaign is saved with its rules, and the videos go to the
                         front of the queue tagged with it (campaign footage counts
                         as permitted, so auto-approve can post it)
"""
from __future__ import annotations

import html as htmlmod
import json
import logging
import re
import threading
from typing import Any

import httpx

from .config import settings
from .db import DB, utcnow

log = logging.getLogger("sxm.finder")

VIDEO_LINK = re.compile(r"https?://(?:www\.|m\.)?(?:youtube\.com/(?:watch\?v=[\w-]{11}|live/[\w-]{11}|shorts/[\w-]{11})|"
                        r"youtu\.be/[\w-]{11}|twitch\.tv/videos/\d+|clips\.twitch\.tv/[\w-]+|twitch\.tv/\w+/clip/[\w-]+|"
                        r"kick\.com/[\w-]+/videos/[\w-]+|kick\.com/[\w-]+\?clip=[\w-]+)[^\s\"'<>)]*", re.I)
CHANNEL_LINK = re.compile(r"https?://(?:www\.)?(?:youtube\.com/(?:@[\w.-]+|channel/UC[\w-]{22})|twitch\.tv/(?!videos)[\w]{3,25}|"
                          r"kick\.com/(?!video)[\w-]{3,25})(?=[/\s\"'<>?]|$)", re.I)
DEFAULT_VIDEOS = 10
DEFAULT_CLIPS = 10


# ---------------- storage ----------------

def _table(db: DB) -> None:
    db.run("""CREATE TABLE IF NOT EXISTS campaign_finds (
        id INTEGER PRIMARY KEY AUTOINCREMENT, url TEXT, prompt TEXT, status TEXT DEFAULT 'working',
        step TEXT, brief TEXT, candidates TEXT, campaign_id INTEGER, error TEXT, created_at TEXT NOT NULL)""")


def get(db: DB, find_id: int) -> dict | None:
    _table(db)
    f = db.one("SELECT * FROM campaign_finds WHERE id=?", (find_id,))
    if f:
        f["brief"] = json.loads(f["brief"] or "{}")
        f["candidates"] = json.loads(f["candidates"] or "[]")
    return f


def recent(db: DB, n: int = 6) -> list[dict]:
    _table(db)
    rows = db.q("SELECT * FROM campaign_finds ORDER BY id DESC LIMIT ?", (n,))
    for r in rows:
        r["brief"] = json.loads(r["brief"] or "{}")
        r["count"] = len(json.loads(r["candidates"] or "[]"))
    return rows


# ---------------- 1. read the brief ----------------

def page_text(url: str, http: httpx.Client) -> tuple[str, str, list[str]]:
    """(title, readable text, every link) from a campaign page."""
    r = http.get(url, follow_redirects=True, headers={"User-Agent": "Mozilla/5.0 (SXM campaign reader)"})
    r.raise_for_status()
    raw = r.text
    title = htmlmod.unescape((re.search(r"<title[^>]*>(.*?)</title>", raw, re.S | re.I) or [None, ""])[1]).strip()
    links = [htmlmod.unescape(h) for h in re.findall(r"href=[\"']([^\"']+)", raw)]
    links += re.findall(r"https?://[^\s\"'<>\\]+", raw)  # links inside scripts / JSON on JS-heavy pages
    body = re.sub(r"(?is)<(script|style|noscript|svg)[^>]*>.*?</\1>", " ", raw)
    body = htmlmod.unescape(re.sub(r"<[^>]+>", " ", body))
    body = re.sub(r"\s+", " ", body).strip()
    # Next.js and similar sites keep the real text in a JSON blob; take its strings too.
    for blob in re.findall(r'(?s)<script[^>]*id="__NEXT_DATA__"[^>]*>(.*?)</script>', raw):
        body += " " + " ".join(s for s in re.findall(r'"([^"\\]{20,400})"', blob) if " " in s)
    return title[:200], body[:20000], list(dict.fromkeys(links))


def _dedupe_links(links: list[str], rx: re.Pattern) -> list[str]:
    out = []
    for l in links:
        for m in rx.finditer(l):
            u = m.group(0).rstrip(".,;")
            if u not in out:
                out.append(u)
    return out


def ask_claude(prompt: str, max_tokens: int = 1500, http: httpx.Client | None = None) -> str:
    http = http or httpx.Client(timeout=90)
    r = http.post("https://api.anthropic.com/v1/messages", headers={
        "x-api-key": settings.anthropic_api_key, "anthropic-version": "2023-06-01", "content-type": "application/json",
    }, json={"model": settings.anthropic_model, "max_tokens": max_tokens,
             "messages": [{"role": "user", "content": prompt}]})
    r.raise_for_status()
    return "".join(b.get("text", "") for b in r.json().get("content", []))


def read_brief(url: str, prompt: str, http: httpx.Client) -> dict:
    title, text, links = ("", "", [])
    if url:
        try:
            title, text, links = page_text(url, http)
        except Exception as e:
            log.warning("campaign page %s: %s", url, e)
            text = f"(Couldn't open the page: {type(e).__name__})"
    videos = _dedupe_links(links + [prompt], VIDEO_LINK)
    channels = _dedupe_links(links + [prompt], CHANNEL_LINK)
    brief: dict[str, Any] = {
        "name": title or (prompt.strip().splitlines()[0][:60] if prompt.strip() else "New campaign"),
        "creators": [], "queries": [], "rules": "", "rate_per_1k": 0, "max_payout": 0, "required_tags": "",
        "platforms": "youtube,instagram", "summary": "", "video_links": videos, "channel_links": channels,
        "read_by": "basic",
    }
    if settings.anthropic_api_key:
        ask = (
            "You read a content-clipping campaign brief (like Content Rewards or Whop clipping campaigns) and "
            "work out what footage a clipper should cut into Shorts for it.\n\n"
            f"Campaign page title: {title!r}\nCampaign page URL: {url!r}\n"
            f"Page text (may be partial):\n{text[:12000]}\n\n"
            f"Links to videos/channels found on the page: {json.dumps((videos + channels)[:40])}\n\n"
            f"What the user said about it: {prompt!r}\n\n"
            "Reply with ONLY a JSON object with keys:\n"
            "name (short campaign name), summary (one sentence: what to clip and why),\n"
            "creators (array of the streamer/creator names whose content to clip, as they appear on YouTube/Twitch/Kick),\n"
            "queries (array of 3 YouTube search queries that find the best long source videos for this campaign: "
            "streams, VODs, podcasts, episodes; be specific, include the creator name),\n"
            "rules (the campaign's rules for clips as short bullet lines: must-include tags, mentions, banned content, "
            "length, captions),\n"
            "rate_per_1k (number, $ paid per 1,000 views, 0 if unknown), max_payout (number, 0 if none),\n"
            "required_tags (space-separated hashtags/mentions the campaign requires), "
            "platforms (comma list from youtube,instagram,facebook,tiktok that the campaign pays for)."
        )
        try:
            out = ask_claude(ask, http=http)
            data = json.loads(out[out.find("{"): out.rfind("}") + 1])
            for k in ("name", "summary", "rules", "required_tags", "platforms"):
                if isinstance(data.get(k), str) and data[k].strip():
                    brief[k] = data[k].strip()
                elif isinstance(data.get(k), list):
                    brief[k] = "\n".join(map(str, data[k]))
            for k in ("creators", "queries"):
                if isinstance(data.get(k), list):
                    brief[k] = [str(x).strip() for x in data[k] if str(x).strip()][:5]
            for k in ("rate_per_1k", "max_payout"):
                try:
                    brief[k] = float(data.get(k) or 0)
                except (TypeError, ValueError):
                    pass
            brief["read_by"] = "claude"
        except Exception as e:
            log.warning("claude brief: %s", e)
    if not brief["queries"]:
        q = re.sub(r"https?://\S+", " ", prompt).strip() or title
        if q:
            brief["queries"] = [q[:100]]
    for ch in channels:  # creators from channel links on the page
        name = ch.rstrip("/").split("/")[-1].lstrip("@")
        if name and name.lower() not in {c.lower() for c in brief["creators"]}:
            brief["creators"].append(name)
    brief["creators"] = brief["creators"][:5]
    return brief


# ---------------- 2. find videos ----------------

def _yt(db: DB, http: httpx.Client):
    from .trending import YT
    return YT(db, http)


def _card(c: dict, why: str) -> dict:
    return {"platform": c["platform"], "id": c["id"], "url": c["url"], "title": c["title"], "channel": c["channel"],
            "thumb": c.get("thumb", ""), "views": c.get("views", 0), "published": c.get("published"),
            "duration_s": c.get("duration_s", 0), "why": why}


def find_videos(db: DB, brief: dict, http: httpx.Client, want: int = 15, step=lambda m: None) -> tuple[list[dict], list[str]]:
    from .trending import card_from_twitch_clip
    found: list[dict] = []
    notes: list[str] = []
    seen: set[str] = set()

    def add(c: dict, why: str) -> None:
        if c["url"] in seen or c["id"] in seen:
            return
        seen.update((c["url"], c["id"]))
        found.append(_card(c, why))

    # a) links the campaign itself gives you: always first
    for u in brief.get("video_links", [])[:20]:
        vid = re.search(r"(?:v=|youtu\.be/|live/|shorts/)([\w-]{11})", u)
        add({"platform": "youtube" if vid else ("twitch" if "twitch" in u else "kick"), "id": vid.group(1) if vid else u,
             "url": u, "title": "Video from the campaign page", "channel": "", "views": 0}, "Listed on the campaign page")

    yt = None
    try:
        yt = _yt(db, http)
    except Exception as e:
        notes.append(str(e))
    if yt:
        # fill in real titles/views for campaign-page YouTube links
        ids = [c["id"] for c in found if c["platform"] == "youtube"]
        if ids:
            try:
                info = {v["id"]: v for v in yt.videos(ids)}
                for c in found:
                    if c["id"] in info:
                        c.update({k: info[c["id"]][k] for k in ("title", "channel", "thumb", "views", "published", "duration_s")})
            except Exception as e:
                notes.append(f"YouTube lookup failed ({type(e).__name__})")
        # b) each creator's channel: their most-viewed long videos from the last 90 days
        for name in brief.get("creators", [])[:3]:
            step(f"Looking through {name}'s YouTube")
            try:
                ch = yt.get("search", part="snippet", type="channel", q=name, maxResults=1)
                if not ch:
                    continue
                cid = ch[0]["id"]["channelId"]
                ids = [i["id"]["videoId"] for i in yt.get("search", part="id", type="video", channelId=cid,
                                                           order="viewCount", maxResults=15,
                                                           publishedAfter=_days_ago(90)) if i["id"].get("videoId")]
                for v in sorted(yt.videos(ids), key=lambda v: v["views"], reverse=True):
                    if v["duration_s"] >= 240:
                        add(v, f"{v['channel']}'s top video lately")
            except Exception as e:
                notes.append(f"YouTube search for {name} failed ({type(e).__name__})")
        # c) searches for the content itself
        for q in brief.get("queries", [])[:3]:
            if len(found) >= want * 2:
                break
            step(f"Searching YouTube: {q}")
            try:
                ids = [i["id"]["videoId"] for i in yt.get("search", part="id", type="video", q=q, order="relevance",
                                                           maxResults=15, videoDuration="long",
                                                           publishedAfter=_days_ago(180)) if i["id"].get("videoId")]
                for v in yt.videos(ids):
                    if v["duration_s"] >= 240:
                        add(v, f"Matches “{q}”")
            except Exception as e:
                notes.append(f"YouTube search “{q}” failed ({type(e).__name__})")
    # d) Twitch: the creators' VODs and their most-watched clips this month
    if settings.twitch_client_id and settings.twitch_client_secret and brief.get("creators"):
        from .discovery import Twitch
        step("Checking Twitch")
        try:
            tw = Twitch(http)
            for login, uid in tw.user_ids([re.sub(r"\W", "", c).lower() for c in brief["creators"][:5]]).items():
                for v in tw.get("videos", user_id=uid, type="archive", first=5, sort="views"):
                    add({"platform": "twitch", "id": v["id"], "url": v["url"], "title": v["title"], "channel": login,
                         "thumb": v.get("thumbnail_url", "").replace("%{width}", "480").replace("%{height}", "270"),
                         "views": v.get("view_count", 0), "published": v.get("created_at"), "duration_s": 0},
                        f"{login}'s Twitch stream")
                for c in tw.get("clips", broadcaster_id=uid, started_at=_days_ago(30), first=5):
                    add(card_from_twitch_clip(c), f"{login}'s top Twitch clip")
        except Exception as e:
            notes.append(f"Twitch failed ({type(e).__name__})")
    # campaign links first, then the most-viewed
    found.sort(key=lambda c: (c["why"] != "Listed on the campaign page", -(c.get("views") or 0)))
    return found, notes


def _days_ago(d: int) -> str:
    from datetime import datetime, timedelta, timezone
    return (datetime.now(timezone.utc) - timedelta(days=d)).strftime("%Y-%m-%dT%H:%M:%SZ")


def run_find(db: DB, find_id: int, http: httpx.Client | None = None) -> None:
    http = http or httpx.Client(timeout=30)
    f = get(db, find_id)
    try:
        db.update("campaign_finds", find_id, step="Reading the campaign")
        brief = read_brief(f["url"] or "", f["prompt"] or "", http)
        db.update("campaign_finds", find_id, brief=json.dumps(brief), step="Finding videos to clip")
        cands, notes = find_videos(db, brief, http, step=lambda m: db.update("campaign_finds", find_id, step=m))
        brief["notes"] = notes
        db.update("campaign_finds", find_id, brief=json.dumps(brief), candidates=json.dumps(cands[:30]),
                  status="ready", step=f"Found {len(cands)} videos")
    except Exception as e:
        log.exception("campaign find")
        db.update("campaign_finds", find_id, status="failed", error=str(e)[:500], step="Something went wrong")


def start(db: DB, url: str, prompt: str, background: bool = True) -> int:
    _table(db)
    url = url.strip()
    if url and not re.match(r"^https?://", url):
        url = "https://" + url
    if not url and not prompt.strip():
        raise ValueError("Paste a campaign link or describe the campaign")
    fid = db.insert("campaign_finds", {"url": url, "prompt": prompt.strip()[:4000], "status": "working",
                                       "step": "Starting", "created_at": utcnow()})
    if background:
        from .db import get_db
        threading.Thread(target=run_find, args=(get_db(), fid), name="sxm-finder", daemon=True).start()
    else:
        run_find(db, fid)
    return fid


# ---------------- 4. make the clips ----------------

def make_clips(db: DB, find_id: int, urls: list[str], layout: str = "stack", total_clips: int = DEFAULT_CLIPS,
               auto_approve: bool = True, name: str = "", min_s=None, max_s=None) -> dict:
    from . import importer
    from .campaigns import add_campaign
    from .discovery import add_source
    f = get(db, find_id)
    if not f:
        raise ValueError("That search is gone; start a new one")
    picked = [c for c in f["candidates"] if c["url"] in set(urls)]
    if not picked:
        raise ValueError("Pick at least one video")
    b = f["brief"]
    cid = f.get("campaign_id")
    if not cid:
        rules = b.get("rules", "")
        if b.get("required_tags"):
            rules = (rules + "\nRequired in every caption: " + b["required_tags"]).strip()
        cid = add_campaign(db, name=(name or b.get("name") or "Campaign")[:80], url=f["url"] or "https://sxm.local/campaign",
                           source_urls="", rate_per_1k=b.get("rate_per_1k") or 0, max_payout=b.get("max_payout") or 0,
                           platforms=b.get("platforms") or "youtube,instagram", rules=rules,
                           required_tags=b.get("required_tags", ""))
        db.update("campaign_finds", find_id, campaign_id=cid)
    total_clips = max(1, min(int(total_clips or DEFAULT_CLIPS), 60))
    picked = picked[:total_clips]  # never fewer than one clip per video
    base, extra = divmod(total_clips, len(picked))
    layout = layout if layout in importer.LAYOUTS else "stack"
    queued = 0
    for i, c in enumerate(picked):
        n = base + (1 if i < extra else 0)  # the first (best) videos get any extra clips
        ext = f"camp{cid}_" + re.sub(r"[^a-zA-Z0-9]+", "_", c["url"])[-100:]
        sid = add_source(db, platform="manual", external_id=ext, url=c["url"], streamer=c.get("channel", ""),
                         title=c.get("title", ""), duration_s=c.get("duration_s") or None, view_count=c.get("views"),
                         campaign_id=cid)
        if sid:
            db.update("sources", sid, priority=20, layout=layout, clips_wanted=n, progress="Waiting in line",
                      **importer.lengths(min_s, max_s))
            queued += n
    if auto_approve:
        db.set_setting("auto_approve", "1")
    importer.wake()
    return {"campaign_id": cid, "videos": len(picked), "clips": queued}
