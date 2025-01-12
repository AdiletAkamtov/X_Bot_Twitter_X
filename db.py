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
        ]
        for sql in migrations:
            try:
                await db.execute(sql)
            except Exception:
                pass  # column already exists
        await db.commit()


async def fetchone(sql: str, params: tuple = ()) -> Optional[dict]:
    async with aiosqlite.connect(_db_path()) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(sql, params) as cur:
            row = await cur.fetchone()
            return dict(row) if row else None


async def fetchall(sql: str, params: tuple = ()) -> list[dict]:
    async with aiosqlite.connect(_db_path()) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(sql, params) as cur:
            rows = await cur.fetchall()
            return [dict(r) for r in rows]


async def execute(sql: str, params: tuple = ()) -> int:
    async with _write_sem():
        async with aiosqlite.connect(_db_path()) as db:
            async with db.execute(sql, params) as cur:
                await db.commit()
                return cur.lastrowid


# ── Accounts ───────────────────────────────────────────────────────────


async def add_account(
    username: str, auth_token_enc: str, ct0_enc: str, proxy_id: Optional[int] = None
) -> int:
    return await execute(
        "INSERT OR REPLACE INTO accounts (username, auth_token, ct0, proxy_id) VALUES (?,?,?,?)",
        (username, auth_token_enc, ct0_enc, proxy_id),
    )


async def get_account(account_id: int) -> Optional[dict]:
    return await fetchone("SELECT * FROM accounts WHERE id=?", (account_id,))


async def get_accounts(active_only: bool = True) -> list[dict]:
    sql = "SELECT * FROM accounts" + (" WHERE active=1" if active_only else "")
    return await fetchall(sql)


async def update_account_last_used(account_id: int) -> None:
    await execute(
        "UPDATE accounts SET last_used=datetime('now') WHERE id=?", (account_id,)
    )


async def delete_account(account_id: int) -> None:
    await execute("DELETE FROM accounts WHERE id=?", (account_id,))


# ── Settings ───────────────────────────────────────────────────────────


async def set_setting(account_id: int, key: str, value: Any) -> None:
    await execute(
        "INSERT OR REPLACE INTO bot_settings (account_id, key, value) VALUES (?,?,?)",
        (account_id, key, json.dumps(value)),
    )


async def get_setting(account_id: int, key: str, default: Any = None) -> Any:
    row = await fetchone(
        "SELECT value FROM bot_settings WHERE account_id=? AND key=?", (account_id, key)
    )
    return json.loads(row["value"]) if row else default


async def get_all_settings(account_id: int) -> dict:
    rows = await fetchall(
        "SELECT key, value FROM bot_settings WHERE account_id=?", (account_id,)
    )
    return {r["key"]: json.loads(r["value"]) for r in rows}


# ── Keywords & Lists ───────────────────────────────────────────────────


async def set_keywords(account_id: int, keywords: list[str]) -> None:
    async with aiosqlite.connect(_db_path()) as db:
        await db.execute("DELETE FROM keywords WHERE account_id=?", (account_id,))
        await db.executemany(
            "INSERT INTO keywords (account_id, keyword) VALUES (?,?)",
            [(account_id, kw) for kw in keywords],
        )
        await db.commit()


async def get_keywords(account_id: int) -> list[str]:
    rows = await fetchall(
        "SELECT keyword FROM keywords WHERE account_id=?", (account_id,)
    )
    return [r["keyword"] for r in rows]


async def set_x_lists(account_id: int, urls: list[str]) -> None:
    async with aiosqlite.connect(_db_path()) as db:
        await db.execute("DELETE FROM x_lists WHERE account_id=?", (account_id,))
        await db.executemany(
            "INSERT INTO x_lists (account_id, list_url) VALUES (?,?)",
            [(account_id, url) for url in urls],
        )
        await db.commit()


async def get_x_lists(account_id: int) -> list[str]:
    rows = await fetchall(
        "SELECT list_url FROM x_lists WHERE account_id=?", (account_id,)
    )
    return [r["list_url"] for r in rows]


# ── Proxies ────────────────────────────────────────────────────────────


