"""Background jobs. APScheduler runs them inside the app process, so starting
the app is all it takes to run unattended."""
from __future__ import annotations

import logging
import traceback

from .analytics import sync_metrics
from .clipper import process_new_sources
from .config import settings
from .db import DB, get_db
from .discovery import run_discovery
from .publishers import publish_due
from .review import auto_approve_pending, schedule_waiting

log = logging.getLogger("sxm.jobs")


def _run(name: str, fn, db: DB | None = None) -> str:
    db = db or get_db()
    if db.get_setting("paused") == "1" and name not in ("analytics", "channel_stats", "trending"):
        return "paused"
    jid = db.log_job(name)
    try:
        msg = fn(db) or "ok"
        db.finish_job(jid, True, msg)
        log.info("%s: %s", name, msg)
        return msg
    except Exception as e:
        db.finish_job(jid, False, f"{e}\n{traceback.format_exc()[-1200:]}")
        log.exception("%s failed", name)
        return f"failed: {e}"


def pipeline(db: DB | None = None) -> str:
    """Find -> clip -> (auto-approve). Clips wait in the review queue otherwise."""
    return " | ".join([
        _run("discover", run_discovery, db),
        _run("clip", process_new_sources, db),
        _run("auto_approve", auto_approve_pending, db),
        _run("schedule_waiting", schedule_waiting, db),
    ])


JOBS = {
    "pipeline": pipeline,
    "publish": lambda db=None: _run("publish", publish_due, db),
    "analytics": lambda db=None: _run("analytics", sync_metrics, db),
    "channel_stats": lambda db=None: _run("channel_stats", _channel_stats, db),
    "trending": lambda db=None: _run("trending", _trending, db),
    "autopost": lambda db=None: _run("autopost", _autopost, db),
}


def _channel_stats(db: DB) -> str:
    from .channel_stats import sync_all
    return sync_all(db)


def _autopost(db: DB) -> str:
    from .autopost import run
    return run(db)


def _trending(db: DB) -> str:
    from .trending import refresh
    data = refresh(db, force=True)
    n = sum(len(v) for r in data.get("rows", {}).values() for v in r.values())
    return f"{n} trending videos" + (f" ({'; '.join(data['notes'])})" if data.get("notes") else "")


def start_scheduler():
    from apscheduler.schedulers.background import BackgroundScheduler
    from apscheduler.triggers.cron import CronTrigger

    sched = BackgroundScheduler(timezone=settings.timezone)
    sched.add_job(JOBS["pipeline"], CronTrigger.from_crontab(settings.discover_cron, timezone=settings.timezone),
                  id="pipeline", max_instances=1, coalesce=True)
    sched.add_job(JOBS["publish"], "interval", minutes=1, id="publish", max_instances=1, coalesce=True)
    sched.add_job(JOBS["analytics"], CronTrigger.from_crontab(settings.analytics_cron, timezone=settings.timezone),
                  id="analytics", max_instances=1, coalesce=True)
    from datetime import datetime, timedelta, timezone
    sched.add_job(JOBS["channel_stats"], "interval", hours=3, id="channel_stats", max_instances=1, coalesce=True,
                  next_run_time=datetime.now(timezone.utc) + timedelta(seconds=20))  # also right after start-up
    sched.add_job(JOBS["autopost"], "interval", minutes=5, id="autopost", max_instances=1, coalesce=True)
    sched.add_job(JOBS["trending"], "interval", hours=max(1.0, settings.trending_refresh_hours), id="trending",
                  max_instances=1, coalesce=True)
    sched.start()
    return sched
