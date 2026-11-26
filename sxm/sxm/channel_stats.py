"""Live channel analytics: the real numbers for every connected account, not
just the clips SXM posted.

YouTube   channel totals (subscribers, lifetime views, videos) from the Data API,
          daily views / watch time / subscribers / likes / revenue for the last
          year from the Analytics API, and the top videos.
Meta      followers and post count for each Instagram account and Facebook Page.

Everything is cached in the database so pages load instantly; a background job
refreshes it every few hours, and the Analytics page has a Refresh button.
"""
from __future__ import annotations

import json
import logging
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone

import httpx

from .config import settings
from .db import DB, utcnow

log = logging.getLogger("sxm.channel_stats")
YT = "https://www.googleapis.com/youtube/v3"
YTA = "https://youtubeanalytics.googleapis.com/v2/reports"
DAILY = ("views", "watch_min", "subs_gained", "subs_lost", "likes", "comments", "shares", "revenue")


def _tables(db: DB) -> None:
    db.run("""CREATE TABLE IF NOT EXISTS channel_daily (
        channel_id INTEGER NOT NULL, day TEXT NOT NULL,
        views INTEGER DEFAULT 0, watch_min REAL DEFAULT 0, subs_gained INTEGER DEFAULT 0,
        subs_lost INTEGER DEFAULT 0, likes INTEGER DEFAULT 0, comments INTEGER DEFAULT 0,
        shares INTEGER DEFAULT 0, revenue REAL DEFAULT 0, PRIMARY KEY (channel_id, day))""")
    db.run("""CREATE TABLE IF NOT EXISTS channel_videos (
        channel_id INTEGER NOT NULL, video_id TEXT NOT NULL, title TEXT, thumb TEXT, published TEXT,
        views INTEGER DEFAULT 0, likes INTEGER DEFAULT 0, comments INTEGER DEFAULT 0,
        period_views INTEGER DEFAULT 0, watch_min REAL DEFAULT 0, duration_s REAL DEFAULT 0,
        PRIMARY KEY (channel_id, video_id))""")


def _extra(ch: dict) -> dict:
    e = ch.get("extra") or {}
    if isinstance(e, dict):
        return dict(e)
    try:
        return json.loads(e)
    except ValueError:
        return {}


def _save_extra(db: DB, ch: dict, **fields) -> None:
    e = _extra(db.get_channel(ch["id"]) or ch)
    e.update(fields)
    db.update("channels", ch["id"], extra=json.dumps(e))


def _ok(r: httpx.Response) -> dict:
    try:
        data = r.json()
    except ValueError:
        data = {}
    if r.status_code >= 400:
        err = data.get("error")
        msg = err.get("message") if isinstance(err, dict) else (err or r.text[:200])
        raise RuntimeError(f"{r.status_code}: {msg}")
    return data


# ---------------- YouTube ----------------

def _report(http: httpx.Client, h: dict, channel_id: str, start: date, end: date, metrics: str, **extra) -> list[dict]:
    rep = _ok(http.get(YTA, headers=h, params={"ids": f"channel=={channel_id}", "startDate": start.isoformat(),
                                               "endDate": end.isoformat(), "metrics": metrics, **extra}))
    cols = [c["name"] for c in rep.get("columnHeaders", [])]
    return [dict(zip(cols, row)) for row in rep.get("rows") or []]


def sync_youtube(db: DB, ch: dict, http: httpx.Client, days: int = 365) -> dict:
    from .discovery import _parse_iso_duration
    from .publishers.youtube import ensure_fresh_token
    h = {"Authorization": f"Bearer {ensure_fresh_token(db, ch, http)}"}
    # 1) lifetime totals, always live
    items = _ok(http.get(f"{YT}/channels", headers=h, params={
        "part": "snippet,statistics,contentDetails", "id": ch["account_id"]})).get("items", [])
    if items:
        st, sn = items[0].get("statistics", {}), items[0].get("snippet", {})
        _save_extra(db, ch, subscribers=int(st.get("subscriberCount", 0) or 0),
                    total_views=int(st.get("viewCount", 0) or 0), videos=int(st.get("videoCount", 0) or 0),
                    hidden_subs=bool(st.get("hiddenSubscriberCount")),
                    uploads=items[0].get("contentDetails", {}).get("relatedPlaylists", {}).get("uploads", ""))
        thumb = ((sn.get("thumbnails") or {}).get("medium") or (sn.get("thumbnails") or {}).get("default") or {}).get("url")
        if thumb:
            db.update("channels", ch["id"], avatar=thumb, name=sn.get("title") or ch["name"])
    notes = []
    # 2) day by day from YouTube Analytics (runs ~2 days behind)
    end, start = date.today(), date.today() - timedelta(days=days)
    full = "views,estimatedMinutesWatched,subscribersGained,subscribersLost,likes,comments,shares"
    rows = []
    try:
        try:
            rows = _report(http, h, ch["account_id"], start, end, full + ",estimatedRevenue", dimensions="day", sort="day")
        except RuntimeError:  # not in the Partner Program: revenue is refused
            rows = _report(http, h, ch["account_id"], start, end, full, dimensions="day", sort="day")
    except RuntimeError as e:
        notes.append(f"daily analytics: {e}")
    for r in rows:
        db.run("""INSERT INTO channel_daily(channel_id, day, views, watch_min, subs_gained, subs_lost, likes,
                  comments, shares, revenue) VALUES (?,?,?,?,?,?,?,?,?,?)
                  ON CONFLICT(channel_id, day) DO UPDATE SET views=excluded.views, watch_min=excluded.watch_min,
                  subs_gained=excluded.subs_gained, subs_lost=excluded.subs_lost, likes=excluded.likes,
                  comments=excluded.comments, shares=excluded.shares, revenue=excluded.revenue""",
               (ch["id"], r["day"], int(r.get("views", 0)), float(r.get("estimatedMinutesWatched", 0)),
                int(r.get("subscribersGained", 0)), int(r.get("subscribersLost", 0)), int(r.get("likes", 0)),
                int(r.get("comments", 0)), int(r.get("shares", 0)), float(r.get("estimatedRevenue", 0) or 0)))
    # 3) top videos of the last 28 days (falls back to the newest uploads)
    top: dict[str, dict] = {}
    try:
        for r in _report(http, h, ch["account_id"], end - timedelta(days=28), end,
                         "views,estimatedMinutesWatched", dimensions="video", sort="-views", maxResults=12):
            top[r["video"]] = {"period_views": int(r.get("views", 0)), "watch_min": float(r.get("estimatedMinutesWatched", 0))}
    except RuntimeError as e:
        notes.append(f"top videos: {e}")
    uploads = _extra(db.get_channel(ch["id"]) or {}).get("uploads")
    if uploads:
        try:
            for it in _ok(http.get(f"{YT}/playlistItems", headers=h, params={
                    "part": "contentDetails", "playlistId": uploads, "maxResults": 12})).get("items", []):
                top.setdefault(it["contentDetails"]["videoId"], {"period_views": 0, "watch_min": 0})
        except RuntimeError as e:
            notes.append(f"uploads: {e}")
    if top:
        vids = _ok(http.get(f"{YT}/videos", headers=h, params={
            "part": "snippet,statistics,contentDetails", "id": ",".join(list(top)[:50])})).get("items", [])
        db.run("DELETE FROM channel_videos WHERE channel_id=?", (ch["id"],))
        for v in vids:
            sn, st = v.get("snippet", {}), v.get("statistics", {})
            th = sn.get("thumbnails") or {}
            t = top.get(v["id"], {})
            db.run("""INSERT OR REPLACE INTO channel_videos(channel_id, video_id, title, thumb, published, views, likes,
                      comments, period_views, watch_min, duration_s) VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                   (ch["id"], v["id"], sn.get("title", ""), (th.get("medium") or th.get("default") or {}).get("url", ""),
                    sn.get("publishedAt"), int(st.get("viewCount", 0) or 0), int(st.get("likeCount", 0) or 0),
                    int(st.get("commentCount", 0) or 0), t.get("period_views", 0), t.get("watch_min", 0),
                    _parse_iso_duration(v.get("contentDetails", {}).get("duration", "PT0S"))))
    return {"days": len(rows), "videos": len(top), "notes": notes}


# ---------------- Meta ----------------

def sync_meta(db: DB, ch: dict, http: httpx.Client) -> dict:
    g = f"https://graph.facebook.com/{settings.meta_graph_version}/{ch['account_id']}"
    fields = "followers_count,media_count,username,profile_picture_url" if ch["platform"] == "instagram" \
        else "followers_count,fan_count,name,picture"
    d = _ok(http.get(g, params={"fields": fields, "access_token": ch["access_token"]}))
    _save_extra(db, ch, subscribers=int(d.get("followers_count") or d.get("fan_count") or 0),
                videos=int(d.get("media_count") or 0))
    # daily reach/views where Meta allows it (needs insights permission; skipped quietly if not)
    try:
        metric = "views" if ch["platform"] == "instagram" else "page_impressions"
        since = int((datetime.now(timezone.utc) - timedelta(days=28)).timestamp())
        ins = _ok(http.get(g + "/insights", params={"metric": metric, "period": "day", "since": since,
                                                    "access_token": ch["access_token"]}))
        for series in ins.get("data", []):
            for v in series.get("values", []):
                day = (v.get("end_time") or "")[:10]
                if day:
                    db.run("""INSERT INTO channel_daily(channel_id, day, views) VALUES (?,?,?)
                              ON CONFLICT(channel_id, day) DO UPDATE SET views=excluded.views""",
                           (ch["id"], day, int(v.get("value") or 0)))
    except Exception:
        pass
    return {"days": 0, "videos": 0, "notes": []}


# ---------------- run ----------------

def sync_all(db: DB, http: httpx.Client | None = None) -> str:
    _tables(db)
    http = http or httpx.Client(timeout=30)
    out = []
    for ch in db.channels():
        if not ch.get("access_token"):
            continue
        try:
            r = (sync_youtube if ch["platform"] == "youtube" else sync_meta)(db, ch, http)
            _save_extra(db, ch, synced_at=utcnow(), sync_error="; ".join(r["notes"])[:300] or None)
            out.append(f"{ch['name']}: {r['days']} days, {r['videos']} videos" + (f" ({'; '.join(r['notes'])})" if r["notes"] else ""))
        except Exception as e:
            log.warning("channel stats %s: %s", ch.get("name"), e)
            _save_extra(db, ch, sync_error=str(e)[:300])
            out.append(f"{ch['name']}: failed ({e})")
    db.set_setting("channel_stats_at", utcnow())
    return "; ".join(out) or "no connected channels"


def last_synced(db: DB) -> str | None:
    return db.get_setting("channel_stats_at") or None


# ---------------- read side ----------------

def _period(days: int) -> tuple[str, str, str]:
    today = date.today()
    start = today - timedelta(days=days - 1)
    prev = start - timedelta(days=days)
    return start.isoformat(), prev.isoformat(), today.isoformat()


def overview(db: DB, days: int = 28, channel_id: int | None = None) -> dict:
    """Everything the Analytics page and Home need, for one channel or all of them."""
    _tables(db)
    chans = [c for c in db.channels() if c.get("access_token") or c.get("enabled")]
    if channel_id:
        chans = [c for c in chans if c["id"] == channel_id]
    ids = [c["id"] for c in chans] or [-1]
    marks = ",".join("?" * len(ids))
    start, prev, today = _period(days)
    rows = db.q(f"SELECT * FROM channel_daily WHERE channel_id IN ({marks}) AND day>=? ORDER BY day", (*ids, prev))
    cur = defaultdict(float)
    old = defaultdict(float)
    by_day: dict[str, dict] = {}
    per_ch: dict[int, dict] = defaultdict(lambda: defaultdict(float))
    per_ch_day: dict[int, dict] = defaultdict(lambda: defaultdict(float))
    for r in rows:
        tgt = cur if r["day"] >= start else old
        for k in DAILY:
            tgt[k] += r[k] or 0
        if r["day"] >= start:
            d = by_day.setdefault(r["day"], {k: 0 for k in DAILY})
            for k in DAILY:
                d[k] += r[k] or 0
            for k in DAILY:
                per_ch[r["channel_id"]][k] += r[k] or 0
            per_ch_day[r["channel_id"]][r["day"]] += r["views"] or 0
    # a full, gap-free series so charts don't jump
    series = []
    d0 = date.fromisoformat(start)
    for i in range(days):
        k = (d0 + timedelta(days=i)).isoformat()
        v = by_day.get(k, {x: 0 for x in DAILY})
        series.append({"day": k, **{x: round(v[x], 2) for x in DAILY},
                       "subs": int(v["subs_gained"] - v["subs_lost"]), "watch_h": round(v["watch_min"] / 60, 2)})
    # campaign + SXM post income
    from .campaigns import estimated_earnings
    camp = estimated_earnings(db)
    campaign_income = round(max(sum(c["estimated"] for c in camp), sum(c["paid"] for c in camp)), 2)

    def delta(k):
        return None if not old.get(k) else round((cur[k] - old[k]) / old[k] * 100)
    subs_total = sum(_extra(c).get("subscribers", 0) for c in chans)
    net = cur["subs_gained"] - cur["subs_lost"]
    old_net = old["subs_gained"] - old["subs_lost"]
    kpis = [
        {"key": "views", "label": "Views", "value": int(cur["views"]), "fmt": "num", "delta": delta("views"), "icon": "👁"},
        {"key": "watch", "label": "Watch hours", "value": round(cur["watch_min"] / 60, 1), "fmt": "dec", "delta": delta("watch_min"), "icon": "⏱"},
        {"key": "subs", "label": "Subscribers", "value": subs_total, "fmt": "num",
         "sub": f"{'+' if net >= 0 else ''}{int(net):,} in {days} days",
         "delta": None if not old_net else round((net - old_net) / abs(old_net) * 100), "icon": "👥"},
        {"key": "likes", "label": "Likes", "value": int(cur["likes"]), "fmt": "num", "delta": delta("likes"), "icon": "❤️"},
        {"key": "income", "label": "Income", "value": round(cur["revenue"] + campaign_income, 2), "fmt": "money",
         "sub": f"${cur['revenue']:,.2f} YouTube · ${campaign_income:,.2f} campaigns", "delta": None, "icon": "💰"},
    ]
    total_views = sum(per_ch[c["id"]]["views"] for c in chans) or 0
    channels = []
    for c in chans:
        e = _extra(c)
        pc = per_ch[c["id"]]
        spark = [int(per_ch_day[c["id"]].get((d0 + timedelta(days=i)).isoformat(), 0)) for i in range(days)]
        channels.append({
            "id": c["id"], "name": c["name"], "platform": c["platform"], "avatar": c.get("avatar") or "",
            "handle": c.get("handle") or "", "enabled": bool(c["enabled"]),
            "subscribers": e.get("subscribers", 0), "total_views": e.get("total_views", 0), "videos": e.get("videos", 0),
            "views": int(pc["views"]), "watch_h": round(pc["watch_min"] / 60, 1),
            "subs_net": int(pc["subs_gained"] - pc["subs_lost"]), "revenue": round(pc["revenue"], 2),
            "share": round(pc["views"] / total_views * 100, 1) if total_views else 0,
            "spark": spark, "synced_at": e.get("synced_at"), "error": e.get("sync_error"),
        })
    channels.sort(key=lambda c: (c["views"], c["subscribers"]), reverse=True)
    vids = db.q(f"""SELECT v.*, c.name channel_name FROM channel_videos v JOIN channels c ON c.id=v.channel_id
                    WHERE v.channel_id IN ({marks}) ORDER BY v.period_views DESC, v.views DESC LIMIT 12""", tuple(ids))
    weekday = defaultdict(lambda: [0, 0])
    for s in series:
        wd = date.fromisoformat(s["day"]).strftime("%a")
        weekday[wd][0] += s["views"]
        weekday[wd][1] += 1
    order = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
    return {
        "days": days, "kpis": kpis, "series": series, "channels": channels, "videos": vids,
        "weekdays": [{"day": d, "avg": round(weekday[d][0] / weekday[d][1]) if weekday[d][1] else 0} for d in order],
        "lifetime_views": sum(c["total_views"] for c in channels),
        "has_data": bool(rows) or any(c["subscribers"] or c["total_views"] for c in channels),
        "synced": last_synced(db),
    }


PALETTE = ["#22d3ee", "#a78bfa", "#f472b6", "#fbbf24", "#34d399", "#60a5fa", "#fb923c", "#e879f9"]
METRICS = ("views", "watch_h", "subs", "likes", "revenue")


def dashboard(db: DB, days: int = 28) -> dict:
    """Everything for the Analytics page, per channel, so the page can combine any set of channels
    (and the previous period, for comparison) instantly without asking the server again."""
    _tables(db)
    chans = [c for c in db.channels() if c.get("access_token") or c.get("enabled")]
    start, prev, _ = _period(days)
    d0, p0 = date.fromisoformat(start), date.fromisoformat(prev)
    labels = [(d0 + timedelta(days=i)).isoformat() for i in range(days)]
    ids = [c["id"] for c in chans] or [-1]
    marks = ",".join("?" * len(ids))
    rows = db.q(f"SELECT * FROM channel_daily WHERE channel_id IN ({marks}) AND day>=?", (*ids, prev))
    data: dict[int, dict] = {c["id"]: {m: [[0.0] * days, [0.0] * days] for m in METRICS} for c in chans}
    for r in rows:
        dd = date.fromisoformat(r["day"])
        cur = (dd - d0).days
        idx, half = (cur, 0) if cur >= 0 else ((dd - p0).days, 1)
        if not 0 <= idx < days or r["channel_id"] not in data:
            continue
        vals = {"views": r["views"] or 0, "watch_h": (r["watch_min"] or 0) / 60,
                "subs": (r["subs_gained"] or 0) - (r["subs_lost"] or 0), "likes": r["likes"] or 0,
                "revenue": r["revenue"] or 0}
        for m, v in vals.items():
            data[r["channel_id"]][m][half][idx] += v
    channels = []
    for i, c in enumerate(chans):
        e = _extra(c)
        d = data[c["id"]]
        channels.append({
            "id": c["id"], "name": c["name"], "platform": c["platform"], "avatar": c.get("avatar") or "",
            "color": PALETTE[i % len(PALETTE)], "subscribers": e.get("subscribers", 0),
            "total_views": e.get("total_views", 0), "videos": e.get("videos", 0),
            "error": e.get("sync_error"), "synced_at": e.get("synced_at"),
            "cur": {m: [round(x, 2) for x in d[m][0]] for m in METRICS},
            "prev": {m: [round(x, 2) for x in d[m][1]] for m in METRICS},
        })
    vids = db.q(f"""SELECT v.channel_id, v.video_id, v.title, v.thumb, v.published, v.views, v.likes, v.comments,
                           v.period_views, v.duration_s, c.name channel_name
                    FROM channel_videos v JOIN channels c ON c.id=v.channel_id
                    WHERE v.channel_id IN ({marks}) ORDER BY v.period_views DESC, v.views DESC LIMIT 80""", tuple(ids))
    from .campaigns import estimated_earnings
    camp = estimated_earnings(db)
    return {
        "days": days, "labels": labels, "channels": channels, "videos": vids,
        "campaign_income": round(max(sum(c["estimated"] for c in camp), sum(c["paid"] for c in camp)), 2),
        "synced": last_synced(db),
    }
