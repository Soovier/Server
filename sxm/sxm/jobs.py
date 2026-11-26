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
from .review import auto_approve_pending

log = logging.getLogger("sxm.jobs")


def _run(name: str, fn, db: DB | None = None) -> str:
    db = db or get_db()
    if db.get_setting("paused") == "1" and name != "analytics":
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
    ])


JOBS = {
    "pipeline": pipeline,
    "publish": lambda db=None: _run("publish", publish_due, db),
    "analytics": lambda db=None: _run("analytics", sync_metrics, db),
}


def start_scheduler():
    from apscheduler.schedulers.background import BackgroundScheduler
    from apscheduler.triggers.cron import CronTrigger

    sched = BackgroundScheduler(timezone=settings.timezone)
    sched.add_job(JOBS["pipeline"], CronTrigger.from_crontab(settings.discover_cron, timezone=settings.timezone),
                  id="pipeline", max_instances=1, coalesce=True)
    sched.add_job(JOBS["publish"], "interval", minutes=1, id="publish", max_instances=1, coalesce=True)
    sched.add_job(JOBS["analytics"], CronTrigger.from_crontab(settings.analytics_cron, timezone=settings.timezone),
                  id="analytics", max_instances=1, coalesce=True)
    sched.start()
    return sched
