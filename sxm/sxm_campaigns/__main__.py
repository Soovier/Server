"""Start SXM Campaigns (website + hourly view checks).

    python -m sxm_campaigns                  # http://localhost:8800 (or $PORT)
    python -m sxm_campaigns run views        # re-check views once and exit
"""
from __future__ import annotations

import logging
import os
import sys


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    from .db import get_db
    from .submissions import refresh_views

    if len(sys.argv) >= 3 and sys.argv[1:3] == ["run", "views"]:
        print(refresh_views(get_db()))
        return

    import uvicorn
    from apscheduler.schedulers.background import BackgroundScheduler

    from .config import settings
    from .web import app

    sched = BackgroundScheduler()
    sched.add_job(lambda: logging.getLogger("sxm_campaigns").info(refresh_views(get_db())), "interval",
                  minutes=settings.views_refresh_minutes, id="views", max_instances=1, coalesce=True)
    sched.start()
    host = os.environ.get("HOST", "0.0.0.0" if os.environ.get("PORT") else "127.0.0.1")
    uvicorn.run(app, host=host, port=int(os.environ.get("PORT", "8800")), proxy_headers=True,
                forwarded_allow_ips="*")


if __name__ == "__main__":
    main()
