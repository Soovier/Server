"""Pull metrics for every post and roll them up per channel and overall."""
from __future__ import annotations

import json
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from .config import settings
from .db import DB, utcnow
from .publishers.meta import facebook_metrics, instagram_metrics
from .publishers.youtube import youtube_metrics

METRIC_FIELDS = ("views", "likes", "comments", "shares", "watch_time_s", "avg_view_s", "revenue_usd")


def _save(db: DB, post_id: int, m: dict) -> None:
    row = {k: m.get(k, 0) for k in METRIC_FIELDS}
    db.insert("metrics", {"post_id": post_id, "captured_at": utcnow(), **row,
                          "raw": json.dumps(m.get("raw", {}))[:4000]})


def sync_metrics(db: DB) -> str:
    posted = db.q("SELECT * FROM posts WHERE status='posted' AND external_id IS NOT NULL")
    done, errors = 0, []
    groups: dict[tuple[str, int | None], list[dict]] = defaultdict(list)
    for p in posted:
        groups[(p["platform"], p.get("channel_id"))].append(p)
    for (platform, ch_id), posts in groups.items():
        channel = db.get_channel(ch_id) if ch_id else None
        if platform in ("instagram", "facebook"):
            fn = instagram_metrics if platform == "instagram" else facebook_metrics
            for p in posts:
                try:
                    _save(db, p["id"], fn(db, p["external_id"], channel=channel))
                    done += 1
                except Exception as e:
                    errors.append(f"{platform[:2]} {p['id']}: {e}")
        elif platform == "youtube":
            try:
                first = min(p["posted_at"] or utcnow() for p in posts)[:10]
                res = youtube_metrics(db, [p["external_id"] for p in posts], date.fromisoformat(first),
                                      channel=channel)
                for p in posts:
                    _save(db, p["id"], res.get(p["external_id"], {}))
                    done += 1
            except Exception as e:
                errors.append(f"yt {(channel or {}).get('name', '')}: {e}")
    from . import campaign_site
    if campaign_site.enabled():
        try:
            campaign_site.push_views(db)
        except Exception as e:
            errors.append(f"campaign site views: {e}")
    return f"synced {done} posts" + (f"; {len(errors)} errors: {errors[:3]}" if errors else "")


# ---------------- read side for the dashboard ----------------

LATEST = """
SELECT p.*, c.hook, c.caption, c.thumb_path, c.campaign_id, ch.name channel_name,
       COALESCE(m.views,0) views, COALESCE(m.likes,0) likes, COALESCE(m.comments,0) comments,
       COALESCE(m.shares,0) shares, COALESCE(m.watch_time_s,0) watch_time_s,
       COALESCE(m.avg_view_s,0) avg_view_s, COALESCE(m.revenue_usd,0) revenue_usd
FROM posts p JOIN clips c ON c.id=p.clip_id LEFT JOIN channels ch ON ch.id=p.channel_id
LEFT JOIN metrics m ON m.id=(SELECT id FROM metrics WHERE post_id=p.id ORDER BY id DESC LIMIT 1)
WHERE p.status='posted'
"""


def post_rows(db: DB, platform: str | None = None, days: int | None = None,
              channel_id: int | None = None) -> list[dict]:
    sql, params = LATEST, []
    if platform:
        sql += " AND p.platform=?"
        params.append(platform)
    if channel_id:
        sql += " AND p.channel_id=?"
        params.append(channel_id)
    if days:
        sql += " AND p.posted_at>=?"
        params.append((datetime.now(timezone.utc) - timedelta(days=days)).isoformat())
    rows = db.q(sql + " ORDER BY p.posted_at DESC", tuple(params))
    tz = ZoneInfo(settings.timezone)
    for r in rows:
        if r["posted_at"]:
            local = datetime.fromisoformat(r["posted_at"]).astimezone(tz)
            r["posted_local"] = local.strftime("%b %d, %Y %I:%M %p")
            r["posted_hour"] = local.hour
            r["posted_weekday"] = local.strftime("%a")
        r["watch_hours"] = round(r["watch_time_s"] / 3600, 2)
        if not r["avg_view_s"] and r["views"]:
            r["avg_view_s"] = r["watch_time_s"] / r["views"]
    return rows


def totals(rows: list[dict]) -> dict:
    t = {"posts": len(rows), "views": 0, "likes": 0, "comments": 0, "shares": 0, "watch_time_s": 0.0,
         "revenue_usd": 0.0}
    for r in rows:
        for k in ("views", "likes", "comments", "shares", "watch_time_s", "revenue_usd"):
            t[k] += r[k] or 0
    t["watch_hours"] = round(t["watch_time_s"] / 3600, 2)
    t["watch_minutes"] = round(t["watch_time_s"] / 60, 1)
    t["avg_view_s"] = round(t["watch_time_s"] / t["views"], 1) if t["views"] else 0
    t["revenue_usd"] = round(t["revenue_usd"], 2)
    t["rpm"] = round(t["revenue_usd"] / t["views"] * 1000, 2) if t["views"] else 0
    return t


def summary(db: DB, days: int | None = None) -> dict:
    from .campaigns import estimated_earnings
    out = {"channels": {}, "combined": None}
    all_rows = []
    for p in ("instagram", "facebook", "youtube"):
        rows = post_rows(db, p, days)
        all_rows += rows
        out["channels"][p] = totals(rows)
    out["combined"] = totals(all_rows)
    # Every connected channel on its own, so several YouTube channels each get a card.
    out["by_channel"] = []
    for ch in db.channels():
        t = totals(post_rows(db, ch["platform"], days, channel_id=ch["id"]))
        out["by_channel"].append({"id": ch["id"], "platform": ch["platform"], "name": ch["name"],
                                  "handle": ch.get("handle") or "", "avatar": ch.get("avatar") or "",
                                  "enabled": bool(ch["enabled"]), **t})
    camp = estimated_earnings(db)
    out["campaign_estimated"] = round(sum(c["estimated"] for c in camp), 2)
    out["campaign_paid"] = round(sum(c["paid"] for c in camp), 2)
    out["combined"]["total_income"] = round(out["combined"]["revenue_usd"] + max(out["campaign_estimated"],
                                                                                 out["campaign_paid"]), 2)
    out["by_hour"] = best_hours(all_rows)
    out["daily"] = daily_views(db, days or 30)
    return out


def best_hours(rows: list[dict]) -> list[dict]:
    """Average views by local posting hour; tells you when to post."""
    agg: dict[int, list[int]] = defaultdict(list)
    for r in rows:
        if "posted_hour" in r:
            agg[r["posted_hour"]].append(r["views"])
    return [{"hour": h, "posts": len(v), "avg_views": round(sum(v) / len(v))} for h, v in sorted(agg.items())]


def daily_views(db: DB, days: int = 30) -> list[dict]:
    """Views gained per day per platform, from consecutive snapshots."""
    since = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    snaps = db.q("""SELECT m.post_id, p.platform, m.captured_at, m.views, m.watch_time_s, m.revenue_usd
                    FROM metrics m JOIN posts p ON p.id=m.post_id WHERE m.captured_at>=?
                    ORDER BY m.post_id, m.id""", (since,))
    last: dict[int, dict] = {}
    agg: dict[tuple[str, str], dict] = defaultdict(lambda: {"views": 0, "watch_time_s": 0.0, "revenue_usd": 0.0})
    for s in snaps:
        prev = last.get(s["post_id"], {"views": 0, "watch_time_s": 0, "revenue_usd": 0})
        day = s["captured_at"][:10]
        a = agg[(day, s["platform"])]
        a["views"] += max(s["views"] - prev["views"], 0)
        a["watch_time_s"] += max(s["watch_time_s"] - prev["watch_time_s"], 0)
        a["revenue_usd"] += max(s["revenue_usd"] - prev["revenue_usd"], 0)
        last[s["post_id"]] = s
    return [{"day": d, "platform": p, **v} for (d, p), v in sorted(agg.items())]
