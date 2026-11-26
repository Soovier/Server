"""SQLite storage. Money is always integer cents."""
from __future__ import annotations

import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    email TEXT NOT NULL UNIQUE,
    name TEXT NOT NULL,
    password_hash TEXT NOT NULL,
    role TEXT NOT NULL,                     -- brand | clipper | admin
    api_token_hash TEXT,
    stripe_account_id TEXT,                 -- clipper's Stripe Connect account
    banned INTEGER DEFAULT 0,
    created_at TEXT NOT NULL
);

-- Clipper social accounts, verified by a code in the bio/description
CREATE TABLE IF NOT EXISTS social_accounts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL REFERENCES users(id),
    platform TEXT NOT NULL,                 -- youtube | instagram | facebook | tiktok
    handle TEXT NOT NULL,
    external_id TEXT,                       -- YouTube channel id etc
    code TEXT NOT NULL,
    verified INTEGER DEFAULT 0,
    verified_by TEXT,                       -- api | admin
    created_at TEXT NOT NULL,
    UNIQUE(platform, handle)
);

CREATE TABLE IF NOT EXISTS campaigns (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    owner_id INTEGER NOT NULL REFERENCES users(id),
    title TEXT NOT NULL,
    description TEXT DEFAULT '',
    source_urls TEXT DEFAULT '',            -- footage clippers may use, one per line
    rules TEXT DEFAULT '',
    required_tags TEXT DEFAULT '',
    platforms TEXT NOT NULL DEFAULT 'youtube,instagram',
    rate_cents_per_1k INTEGER NOT NULL,     -- pay per 1,000 views
    max_cents_per_post INTEGER DEFAULT 0,   -- 0 = no cap
    min_views INTEGER DEFAULT 0,            -- views before a post starts earning
    budget_cents INTEGER NOT NULL,          -- what the brand intends to spend
    funded_cents INTEGER DEFAULT 0,         -- money actually received, after platform fee
    status TEXT NOT NULL DEFAULT 'draft',   -- draft | active | paused | ended
    ends_at TEXT,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS fundings (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    campaign_id INTEGER NOT NULL REFERENCES campaigns(id),
    gross_cents INTEGER NOT NULL,
    fee_cents INTEGER NOT NULL,
    net_cents INTEGER NOT NULL,
    method TEXT NOT NULL,                   -- stripe | manual
    reference TEXT UNIQUE,                  -- Stripe checkout session id
    status TEXT NOT NULL DEFAULT 'pending', -- pending | paid
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS submissions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    campaign_id INTEGER NOT NULL REFERENCES campaigns(id),
    clipper_id INTEGER NOT NULL REFERENCES users(id),
    platform TEXT NOT NULL,
    post_url TEXT NOT NULL UNIQUE,
    external_id TEXT,
    status TEXT NOT NULL DEFAULT 'pending', -- pending | approved | rejected
    views INTEGER DEFAULT 0,                -- views that count toward pay
    reported_views INTEGER DEFAULT 0,       -- clipper-reported, awaiting brand confirmation
    views_source TEXT DEFAULT 'none',       -- api | reported
    proof_path TEXT,                        -- screenshot for reported views
    earned_cents INTEGER DEFAULT 0,
    note TEXT,
    posted_at TEXT,
    submitted_at TEXT NOT NULL,
    reviewed_at TEXT,
    views_checked_at TEXT
);

CREATE TABLE IF NOT EXISTS payouts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    clipper_id INTEGER NOT NULL REFERENCES users(id),
    amount_cents INTEGER NOT NULL,
    method TEXT NOT NULL,                   -- stripe | manual
    status TEXT NOT NULL DEFAULT 'requested', -- requested | paid | rejected
    reference TEXT,
    details TEXT,                           -- e.g. PayPal email for manual payouts
    created_at TEXT NOT NULL,
    paid_at TEXT
);
"""

_lock = threading.RLock()


def utcnow() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


class DB:
    def __init__(self, path: Path | str):
        self.path = str(path)
        if self.path != ":memory:":
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(self.path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys = ON")
        if self.path != ":memory:":
            self.conn.execute("PRAGMA journal_mode = WAL")
        self.conn.executescript(SCHEMA)
        self.conn.commit()

    @contextmanager
    def tx(self) -> Iterator[sqlite3.Connection]:
        with _lock:
            try:
                yield self.conn
                self.conn.commit()
            except Exception:
                self.conn.rollback()
                raise

    def q(self, sql: str, params: tuple = ()) -> list[dict[str, Any]]:
        with _lock:
            return [dict(r) for r in self.conn.execute(sql, params).fetchall()]

    def one(self, sql: str, params: tuple = ()) -> dict[str, Any] | None:
        rows = self.q(sql, params)
        return rows[0] if rows else None

    def run(self, sql: str, params: tuple = ()) -> int:
        with self.tx() as c:
            return c.execute(sql, params).lastrowid

    def insert(self, table: str, row: dict[str, Any]) -> int:
        cols, qs = ",".join(row), ",".join("?" for _ in row)
        return self.run(f"INSERT INTO {table}({cols}) VALUES ({qs})", tuple(row.values()))

    def update(self, table: str, row_id: int, **fields: Any) -> None:
        if fields:
            sets = ",".join(f"{k}=?" for k in fields)
            self.run(f"UPDATE {table} SET {sets} WHERE id=?", (*fields.values(), row_id))


_db: DB | None = None


def get_db() -> DB:
    global _db
    if _db is None:
        from .config import settings
        settings.upload_dir.mkdir(parents=True, exist_ok=True)
        _db = DB(settings.db_path)
    return _db


def set_db(db: DB) -> None:
    global _db
    _db = db
