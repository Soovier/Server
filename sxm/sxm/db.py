"""SQLite storage. One file, no server to run."""
from __future__ import annotations

import json
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

SCHEMA = """
CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

-- OAuth tokens and account ids for each connected platform
CREATE TABLE IF NOT EXISTS accounts (
    platform TEXT PRIMARY KEY,            -- instagram | facebook | youtube
    account_id TEXT,                      -- IG user id, FB page id, YT channel id
    name TEXT,
    access_token TEXT,
    refresh_token TEXT,
    expires_at TEXT,
    extra TEXT DEFAULT '{}',
    connected_at TEXT
);

-- Long-form source videos found by discovery (streams, VODs, campaign sources)
CREATE TABLE IF NOT EXISTS sources (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    platform TEXT NOT NULL,               -- twitch | youtube | kick | campaign | manual
    external_id TEXT NOT NULL,
    streamer TEXT,
    title TEXT,
    url TEXT NOT NULL,
    duration_s REAL,
    view_count INTEGER,
    campaign_id INTEGER,
    status TEXT NOT NULL DEFAULT 'new',   -- new | downloading | downloaded | clipped | failed | skipped
    local_path TEXT,
    error TEXT,
    found_at TEXT NOT NULL,
    UNIQUE(platform, external_id)
);

CREATE TABLE IF NOT EXISTS clips (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source_id INTEGER NOT NULL REFERENCES sources(id),
    campaign_id INTEGER REFERENCES campaigns(id),
    start_s REAL NOT NULL,
    end_s REAL NOT NULL,
    score REAL DEFAULT 0,
    hook TEXT,                            -- on-screen hook text
    caption TEXT,                         -- post caption
    hashtags TEXT,
    transcript TEXT,
    video_path TEXT,
    thumb_path TEXT,
    status TEXT NOT NULL DEFAULT 'pending_review',
        -- rendering | pending_review | approved | rejected | scheduled | posted | failed
    review_note TEXT,
    created_at TEXT NOT NULL,
    reviewed_at TEXT
);

-- One row per clip per platform. Holds schedule + result.
CREATE TABLE IF NOT EXISTS posts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    clip_id INTEGER NOT NULL REFERENCES clips(id),
    platform TEXT NOT NULL,               -- instagram | facebook | youtube
    scheduled_at TEXT NOT NULL,           -- UTC ISO
    status TEXT NOT NULL DEFAULT 'scheduled',  -- scheduled | publishing | posted | failed | cancelled
    external_id TEXT,
    permalink TEXT,
    posted_at TEXT,
    error TEXT,
    attempts INTEGER DEFAULT 0,
    UNIQUE(clip_id, platform)
);

-- Daily metric snapshots per post. Latest row per post = current totals.
CREATE TABLE IF NOT EXISTS metrics (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    post_id INTEGER NOT NULL REFERENCES posts(id),
    captured_at TEXT NOT NULL,
    views INTEGER DEFAULT 0,
    likes INTEGER DEFAULT 0,
    comments INTEGER DEFAULT 0,
    shares INTEGER DEFAULT 0,
    watch_time_s REAL DEFAULT 0,          -- total time watched, seconds
    avg_view_s REAL DEFAULT 0,
    revenue_usd REAL DEFAULT 0,
    raw TEXT
);

CREATE TABLE IF NOT EXISTS campaigns (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    url TEXT NOT NULL,                    -- Content Rewards campaign page
    source_urls TEXT DEFAULT '',          -- newline separated content the campaign allows
    rate_per_1k REAL DEFAULT 0,           -- $ per 1,000 views
    max_payout REAL DEFAULT 0,
    platforms TEXT DEFAULT 'instagram,youtube',
    rules TEXT DEFAULT '',
    required_tags TEXT DEFAULT '',
    active INTEGER DEFAULT 1,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS submissions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    campaign_id INTEGER NOT NULL REFERENCES campaigns(id),
    post_id INTEGER NOT NULL REFERENCES posts(id),
    status TEXT NOT NULL DEFAULT 'ready', -- ready | submitted | approved | rejected | paid
    submitted_at TEXT,
    payout_usd REAL DEFAULT 0,
    note TEXT,
    UNIQUE(campaign_id, post_id)
);

CREATE TABLE IF NOT EXISTS job_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    job TEXT NOT NULL,
    started_at TEXT NOT NULL,
    finished_at TEXT,
    ok INTEGER,
    message TEXT
);
"""

DEFAULT_SETTINGS = {
    "auto_approve": "1",            # on: clips that pass the checks post without manual review
    "auto_approve_min_score": "0",  # only auto-approve clips scoring at least this
    "post_platforms": "instagram,facebook,youtube",
    "paused": "0",
}

_lock = threading.RLock()


def utcnow() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


class DB:
    def __init__(self, path: Path | str):
        self.path = str(path)
        if self.path != ":memory:":
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(self.path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA foreign_keys = ON")
        self._conn.execute("PRAGMA journal_mode = WAL") if self.path != ":memory:" else None
        self._conn.executescript(SCHEMA)
        self._migrate()
        for k, v in DEFAULT_SETTINGS.items():
            self._conn.execute("INSERT OR IGNORE INTO settings(key, value) VALUES (?, ?)", (k, v))
        self._conn.commit()

    def _migrate(self) -> None:
        """Add columns introduced after the first release."""
        adds = {
            "campaigns": {"site_campaign_id": "INTEGER"},
            "submissions": {"site_submission_id": "INTEGER"},
        }
        for table, cols in adds.items():
            have = {r[1] for r in self._conn.execute(f"PRAGMA table_info({table})")}
            for col, typ in cols.items():
                if col not in have:
                    self._conn.execute(f"ALTER TABLE {table} ADD COLUMN {col} {typ}")

    @contextmanager
    def tx(self) -> Iterator[sqlite3.Connection]:
        with _lock:
            try:
                yield self._conn
                self._conn.commit()
            except Exception:
                self._conn.rollback()
                raise

    def q(self, sql: str, params: tuple | dict = ()) -> list[dict[str, Any]]:
        with _lock:
            return [dict(r) for r in self._conn.execute(sql, params).fetchall()]

    def one(self, sql: str, params: tuple | dict = ()) -> dict[str, Any] | None:
        rows = self.q(sql, params)
        return rows[0] if rows else None

    def run(self, sql: str, params: tuple | dict = ()) -> int:
        with self.tx() as c:
            cur = c.execute(sql, params)
            return cur.lastrowid

    def insert(self, table: str, row: dict[str, Any], ignore: bool = False) -> int:
        cols = ",".join(row)
        qs = ",".join("?" for _ in row)
        verb = "INSERT OR IGNORE" if ignore else "INSERT"
        return self.run(f"{verb} INTO {table}({cols}) VALUES ({qs})", tuple(row.values()))

    def update(self, table: str, row_id: int, **fields: Any) -> None:
        if not fields:
            return
        sets = ",".join(f"{k}=?" for k in fields)
        self.run(f"UPDATE {table} SET {sets} WHERE id=?", (*fields.values(), row_id))

    # settings helpers
    def get_setting(self, key: str, default: str = "") -> str:
        row = self.one("SELECT value FROM settings WHERE key=?", (key,))
        return row["value"] if row else default

    def set_setting(self, key: str, value: str) -> None:
        self.run("INSERT INTO settings(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                 (key, value))

    # account helpers
    def get_account(self, platform: str) -> dict[str, Any] | None:
        acc = self.one("SELECT * FROM accounts WHERE platform=?", (platform,))
        if acc:
            acc["extra"] = json.loads(acc.get("extra") or "{}")
        return acc

    def save_account(self, platform: str, **fields: Any) -> None:
        if "extra" in fields and not isinstance(fields["extra"], str):
            fields["extra"] = json.dumps(fields["extra"])
        fields.setdefault("connected_at", utcnow())
        existing = self.one("SELECT platform FROM accounts WHERE platform=?", (platform,))
        if existing:
            sets = ",".join(f"{k}=?" for k in fields)
            self.run(f"UPDATE accounts SET {sets} WHERE platform=?", (*fields.values(), platform))
        else:
            self.insert("accounts", {"platform": platform, **fields})

    def log_job(self, job: str) -> int:
        return self.insert("job_log", {"job": job, "started_at": utcnow()})

    def finish_job(self, job_id: int, ok: bool, message: str = "") -> None:
        self.update("job_log", job_id, finished_at=utcnow(), ok=int(ok), message=message[:2000])


_db: DB | None = None


def get_db() -> DB:
    global _db
    if _db is None:
        from .config import settings
        settings.ensure_dirs()
        _db = DB(settings.db_path)
    return _db


def set_db(db: DB) -> None:
    """Used by tests."""
    global _db
    _db = db
