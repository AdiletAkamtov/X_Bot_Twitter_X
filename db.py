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


async def add_proxy(url: str, ptype: str = "http") -> int:
    return await execute(
        "INSERT OR REPLACE INTO proxies (url, ptype) VALUES (?,?)", (url, ptype)
    )


async def get_proxies(active_only: bool = True) -> list[dict]:
    sql = "SELECT * FROM proxies"
    if active_only:
        sql += " WHERE active=1 ORDER BY fail_count ASC, last_used ASC"
    return await fetchall(sql)


async def mark_proxy_failed(proxy_id: int) -> None:
    await execute("UPDATE proxies SET fail_count=fail_count+1 WHERE id=?", (proxy_id,))


async def reset_proxy_fails(proxy_id: int) -> None:
    await execute(
        "UPDATE proxies SET fail_count=0, last_used=datetime('now') WHERE id=?",
        (proxy_id,),
    )


# ── Posts log ──────────────────────────────────────────────────────────


async def log_post(
    account_id: int,
    post_id: str,
    post_url: str,
    post_text: str,
    comment_id: str,
    comment_text: str,
    reply_text: str,
    reply_variant2: str,
    ai_provider: str,
    sleep_seconds: float = 0.0,
) -> int:
    return await execute(
        """INSERT OR IGNORE INTO posts_log
           (account_id, post_id, post_url, post_text, comment_id, comment_text,
            reply_text, reply_variant2, ai_provider, sleep_seconds)
           VALUES (?,?,?,?,?,?,?,?,?,?)""",
        (
            account_id,
            post_id,
            post_url,
            post_text,
            comment_id,
            comment_text,
            reply_text,
            reply_variant2,
            ai_provider,
            sleep_seconds,
        ),
    )


async def update_log_status(log_id: int, status: str) -> None:
    posted_at = "datetime('now')" if status == "posted" else "NULL"
    await execute(
        f"UPDATE posts_log SET status=?, posted_at={posted_at} WHERE id=?",
        (status, log_id),
    )


async def was_already_replied(account_id: int, post_id: str, comment_id: str) -> bool:
    row = await fetchone(
        "SELECT id FROM posts_log WHERE account_id=? AND post_id=? AND comment_id=? AND status='posted'",
        (account_id, post_id, comment_id),
    )
    return row is not None


async def was_replied_any(account_id: int, post_id: str) -> bool:
    """Returns True if we already replied to this post (to the post itself OR any comment on it)."""
    row = await fetchone(
        "SELECT id FROM posts_log WHERE account_id=? AND post_id=? AND status='posted'",
        (account_id, post_id),
    )
    return row is not None


# ── Daily stats ────────────────────────────────────────────────────────


async def increment_daily_count(account_id: int) -> int:
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    async with aiosqlite.connect(_db_path()) as db:
        await db.execute(
            """INSERT INTO daily_stats (account_id, date, count) VALUES (?,?,1)
               ON CONFLICT(account_id, date) DO UPDATE SET count=count+1""",
            (account_id, today),
        )
        await db.commit()
        async with db.execute(
            "SELECT count FROM daily_stats WHERE account_id=? AND date=?",
            (account_id, today),
        ) as cur:
            row = await cur.fetchone()
            return row[0] if row else 0


async def get_daily_count(account_id: int) -> int:
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    row = await fetchone(
        "SELECT count FROM daily_stats WHERE account_id=? AND date=?",
        (account_id, today),
    )
    return row["count"] if row else 0


# ── Allowed users ──────────────────────────────────────────────────────


async def add_allowed_user(telegram_id: int, label: str = "") -> None:
    await execute(
        "INSERT OR REPLACE INTO allowed_users (telegram_id, label) VALUES (?,?)",
        (telegram_id, label),
    )


async def remove_allowed_user(telegram_id: int) -> None:
    await execute("DELETE FROM allowed_users WHERE telegram_id=?", (telegram_id,))


async def get_allowed_users() -> list[dict]:
    return await fetchall("SELECT * FROM allowed_users ORDER BY added_at DESC")
