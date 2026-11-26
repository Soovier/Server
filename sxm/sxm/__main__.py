"""Start SXM: dashboard + background scheduler.

    python -m sxm            # dashboard at http://localhost:8787, jobs run on schedule
    python -m sxm run pipeline|publish|analytics   # run one job now and exit
"""
from __future__ import annotations

import logging
import sys


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    if len(sys.argv) >= 3 and sys.argv[1] == "run":
        from .jobs import JOBS
        print(JOBS[sys.argv[2]]())
        return
    import uvicorn
    from urllib.parse import urlparse

    from .config import settings
    from .jobs import start_scheduler
    from .web import app

    start_scheduler()
    port = urlparse(settings.base_url).port or 8787
    uvicorn.run(app, host="127.0.0.1", port=port)


if __name__ == "__main__":
    main()
