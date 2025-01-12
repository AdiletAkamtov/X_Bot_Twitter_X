"""
db.py — Async SQLite storage (aiosqlite)
Tables: accounts, proxies, bot_settings, keywords, x_lists, posts_log, daily_stats
"""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

import aiosqlite

from config import get_settings

_DB_PATH: Optional[Path] = None
_WRITE_SEM: Optional[asyncio.Semaphore] = None


def _write_sem() -> asyncio.Semaphore:
    """Return the per-event-loop write semaphore (created lazily)."""
    global _WRITE_SEM
    if _WRITE_SEM is None:
        _WRITE_SEM = asyncio.Semaphore(1)
    return _WRITE_SEM


def _db_path() -> Path:
    global _DB_PATH
    if _DB_PATH is None:
        _DB_PATH = get_settings().db_path
    return _DB_PATH


SCHEMA = """
PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;

CREATE TABLE IF NOT EXISTS accounts (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    username    TEXT    NOT NULL UNIQUE,
    auth_token  TEXT    NOT NULL,
    ct0         TEXT    NOT NULL,
    proxy_id    INTEGER REFERENCES proxies(id) ON DELETE SET NULL,
    active      INTEGER NOT NULL DEFAULT 1,
    created_at  TEXT    NOT NULL DEFAULT (datetime('now')),
    last_used   TEXT
);

CREATE TABLE IF NOT EXISTS proxies (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    url         TEXT    NOT NULL UNIQUE,
    ptype       TEXT    NOT NULL DEFAULT 'http',
    active      INTEGER NOT NULL DEFAULT 1,
    last_used   TEXT,
    fail_count  INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS bot_settings (
    account_id  INTEGER NOT NULL REFERENCES accounts(id) ON DELETE CASCADE,
    key         TEXT    NOT NULL,
    value       TEXT    NOT NULL,
    PRIMARY KEY (account_id, key)
);

CREATE TABLE IF NOT EXISTS keywords (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    account_id  INTEGER NOT NULL REFERENCES accounts(id) ON DELETE CASCADE,
    keyword     TEXT    NOT NULL
);

CREATE TABLE IF NOT EXISTS x_lists (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    account_id  INTEGER NOT NULL REFERENCES accounts(id) ON DELETE CASCADE,
    list_url    TEXT    NOT NULL
);

CREATE TABLE IF NOT EXISTS posts_log (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    account_id      INTEGER NOT NULL REFERENCES accounts(id) ON DELETE CASCADE,
    post_id         TEXT    NOT NULL,
    post_url        TEXT,
    post_text       TEXT,
    comment_id      TEXT,
    comment_text    TEXT,
    reply_text      TEXT,
    reply_variant2  TEXT,
    status          TEXT    NOT NULL DEFAULT 'pending',
    ai_provider     TEXT,
    sleep_seconds   REAL,
    created_at      TEXT    NOT NULL DEFAULT (datetime('now')),
    posted_at       TEXT,
    UNIQUE(account_id, post_id, comment_id)
);

CREATE TABLE IF NOT EXISTS daily_stats (
    account_id  INTEGER NOT NULL REFERENCES accounts(id) ON DELETE CASCADE,
    date        TEXT    NOT NULL,
    count       INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (account_id, date)
);

CREATE TABLE IF NOT EXISTS allowed_users (
    telegram_id INTEGER PRIMARY KEY,
    label       TEXT,
    added_at    TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_keywords_account  ON keywords(account_id);
CREATE INDEX IF NOT EXISTS idx_x_lists_account   ON x_lists(account_id);
CREATE INDEX IF NOT EXISTS idx_posts_log_account ON posts_log(account_id);
CREATE INDEX IF NOT EXISTS idx_accounts_proxy    ON accounts(proxy_id);
"""


# ── Low-level helpers ──────────────────────────────────────────────────


async def init_db() -> None:
    async with aiosqlite.connect(_db_path()) as db:
        await db.executescript(SCHEMA)
        # Migrations: add columns if they don't exist yet
        migrations = [
            "ALTER TABLE posts_log ADD COLUMN post_url TEXT",
            "ALTER TABLE posts_log ADD COLUMN reply_variant2 TEXT",
            "ALTER TABLE posts_log ADD COLUMN sleep_seconds REAL",
