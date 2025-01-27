"""
main.py — X AutoReply Bot
Entry point, worker loop, worker manager, CLI utilities.

Run:
  python main.py              → start bot
  python main.py genkey       → generate encryption key
  python main.py add_account  → add X account via CLI
  python main.py list_accounts
  python main.py test_session <id>
  python main.py reset_daily  <id>
  python main.py add_proxy    <url>
"""

from __future__ import annotations

import os
import sys

# ── FIX: Windows cp1251 → UTF-8 (arrow chars in loguru format crash on CIS Windows) ──
if sys.platform == "win32":
    os.environ.setdefault("PYTHONIOENCODING", "utf-8")
    os.environ.setdefault("PYTHONUTF8", "1")
    try:
        if hasattr(sys.stdout, "reconfigure"):
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        if hasattr(sys.stderr, "reconfigure"):
            sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
# ──────────────────────────────────────────────────────────────────────────────

import asyncio
import random
import signal
import time

import state

# Logger noise-suppression is handled centrally in config._setup_logger().
from ai import generate_reply
from config import (
    BotDefaults,
    compose_delay,
    generate_key,
    logger,
    rate_limiter,
    read_delay,
)
from db import (
    get_account,
    get_accounts,
    get_all_settings,
    get_daily_count,
    get_keywords,
    get_x_lists,
    increment_daily_count,
    init_db,
    log_post,
    update_account_last_used,
    update_log_status,
    was_already_replied,
    was_replied_any,
)
from proxy import proxy_manager
from tg_bot import (
    build_application,
    register_handlers,
    send_approval_request,
    send_posted_notification,
)
from twitter import TwitterClient

# ─────────────────────────────────────────────
# PENDING QUEUE (manual Telegram approval)
# ─────────────────────────────────────────────

# Aliases for backwards-compatibility within this module
_pending_queue = state.pending_queue
_pending_callbacks = state.pending_callbacks


def register_pending_callback(cb) -> None:
    state.pending_callbacks.append(cb)


async def _notify_pending(item: dict) -> None:
    for cb in state.pending_callbacks:
        try:
            await cb(item)
        except Exception as e:
            logger.error(f"Pending callback error: {e}")


# ─────────────────────────────────────────────
# BOT WORKER
# ─────────────────────────────────────────────


class BotWorker:
    def __init__(self, account_id: int):
        self.account_id = account_id
        self._stop_event = asyncio.Event()
        self._wake_event = asyncio.Event()  # set() прерывает любой сон
        self._cycle_num = 0
        self.next_post_at: float = 0.0  # unix timestamp следующего цикла
        self.is_sleeping: bool = False  # True = спит, False = цикл активен
        self._manual_sleep_until: float = 0.0  # ручной сон до этого timestamp
        self._posting_lock = asyncio.Lock()  # held while TG manual post in progress

    def stop(self) -> None:
        self._stop_event.set()
        self._wake_event.set()  # прерываем текущий сон при остановке

    async def _interruptible_sleep(self, seconds: float) -> None:
        """Sleep for `seconds`, but wake immediately if _wake_event is already set or gets set."""
        # If force_wake fired while we were in _cycle() — the event is already set.
        # Don't clear it first, just check and return immediately.
        if self._wake_event.is_set():
            self._wake_event.clear()
            return
        self._wake_event.clear()
        try:
            await asyncio.wait_for(self._wake_event.wait(), timeout=seconds)
        except asyncio.TimeoutError:
            pass
        self._wake_event.clear()

    async def run(self) -> None:
        logger.info(f"[Worker:{self.account_id}] Starting...")
        account = await get_account(self.account_id)
        if not account or not account["active"]:
            logger.warning(f"[Worker:{self.account_id}] Account not found or inactive")
            return

        while not self._stop_event.is_set():
            # ── Ручной сон (задан через TG) ──────────────────────────
            extra_wait = self._manual_sleep_until - time.time()
            if extra_wait > 0:
                h, m = divmod(int(extra_wait // 60), 60)
                label = f"{h}ч {m}м" if h else f"{m}м"
                logger.info(f"[Worker:{self.account_id}] 😴 Manual sleep {label}")
                self.is_sleeping = True
                self.next_post_at = self._manual_sleep_until
                self._manual_sleep_until = 0.0
                await self._interruptible_sleep(extra_wait)
                self.is_sleeping = False
                if self._stop_event.is_set():
                    return
                logger.info(
                    f"[Worker:{self.account_id}] ⏰ Manual sleep ended — resuming"
                )
                continue

            try:
                # Reload account each cycle — picks up fresh auth_token/ct0 if updated
                account = await get_account(self.account_id)
                if not account or not account["active"]:
                    logger.warning(
                        f"[Worker:{self.account_id}] Account deactivated — stopping"
                    )
                    return
                # Wait if TG manual post is in progress for this account
                if self._posting_lock.locked():
                    logger.info(
                        f"[Worker:{self.account_id}] ⏸ Ждём публикации через TG..."
                    )
                    await self._posting_lock.acquire()
                    self._posting_lock.release()
                # License check every cycle
                await _check_license()
                await self._cycle(account)
            except Exception as e:
                logger.error(
                    f"[Worker:{self.account_id}] Cycle error: {e}", exc_info=True
                )

            if self._stop_event.is_set():
                return

            settings = await get_all_settings(self.account_id)
            base_delay = min(
                _int(settings.get("min_delay"), BotDefaults.min_delay_seconds), 3600
            )

            jitter_range = base_delay * 0.33
            jitter = random.uniform(-jitter_range, jitter_range)
            sleep_s = max(90, base_delay + jitter)
            self.next_post_at = time.time() + sleep_s
            self.is_sleeping = True
            logger.info(
                f"[Worker:{self.account_id}] 💤 Next cycle in {sleep_s / 60:.1f}min "
                f"(base={base_delay // 60}min ± {jitter_range / 60:.1f}min)"
            )
            await self._interruptible_sleep(sleep_s)
            self.is_sleeping = False

    async def _cycle(self, account: dict) -> None:
        settings = await get_all_settings(self.account_id)
        daily_limit = _int(settings.get("daily_limit"), BotDefaults.daily_comment_limit)
        today_count = await get_daily_count(self.account_id)

        logger.info(
            f"[Worker:{self.account_id}] ─── Cycle start | "
            f"mode={settings.get('search_mode', '?')} | "
            f"today={today_count}/{daily_limit} | "
            f"AI={settings.get('ai_provider', 'default')} | "
            f"auto_publish={settings.get('auto_publish', False)}"
        )

        if today_count >= daily_limit:
            logger.info(
                f"[Worker:{self.account_id}] Daily limit reached ({today_count}/{daily_limit}). Sleeping 1h."
            )
            await self._interruptible_sleep(3600)
            return

        active_h_start = _int(
            settings.get("active_hours_start"), BotDefaults.active_hours_start
        )
        active_h_end = _int(
            settings.get("active_hours_end"), BotDefaults.active_hours_end
        )
        outside_sleep = _int(
            settings.get("outside_sleep_min"), BotDefaults.outside_sleep_min
        )
        if not await rate_limiter.wait_if_needed(
            self.account_id,
            daily_limit,
            active_hours_start=active_h_start,
            active_hours_end=active_h_end,
            outside_sleep_min=outside_sleep,
        ):
            await asyncio.sleep(3600)
            return

        proxy = await proxy_manager.get_proxy_for_account(account.get("proxy_id"))

        async with TwitterClient(
            account_id=self.account_id,
            auth_token_enc=account["auth_token"],
            ct0_enc=account["ct0"],
            proxy=proxy,
        ) as client:
            username = await client.verify_session()
            if not username:
                logger.error(f"[Worker:{self.account_id}] Session invalid! Stopping.")
                self.stop()
                return

            tweets = await self._fetch_tweets(client, settings)
            if not tweets:
                logger.warning(
                    f"[Worker:{self.account_id}] No tweets found after all methods. "
                    f"mode={settings.get('search_mode', '?')} min_likes={_int(settings.get('min_likes'), BotDefaults.min_likes)}. "
                    f"Try lowering min_likes in Settings."
                )
                return

            # Sort tweets by likes desc — pick the best one first
            tweets.sort(key=lambda t: t.likes, reverse=True)

            system_prompt = settings.get("system_prompt", BotDefaults.system_prompt)
            ai_provider = settings.get("ai_provider") or None
            # If per-account provider not set, use global default from .env
            if not ai_provider:
                from config import get_settings as _gs

                ai_provider = _gs().default_ai_provider
            auto_publish = settings.get("auto_publish", BotDefaults.auto_publish)
            sort_by = settings.get("comment_sort", BotDefaults.comment_sort)

            # ── Режим чередования: чётный цикл → пост, нечётный → комментарий ─
            # reply_mode="post_only" отключает режим B полностью.
            reply_mode = settings.get("reply_mode", "hybrid")
            self._cycle_num += 1
            if reply_mode == "post_only":
                mode_reply = "post"
            else:
                mode_reply = "post" if self._cycle_num % 2 == 1 else "comment"
            logger.info(
                f"[Worker:{self.account_id}] Цикл #{self._cycle_num} | "
                f"режим={'📝 на пост' if mode_reply == 'post' else '💬 на комментарий'} | "
                f"всего постов: {len(tweets)}"
            )

            chosen_tweet = None
            chosen_comment = None  # None = отвечаем на сам пост

            if mode_reply == "post":
                # ── Режим A: ответ прямо на пост, ещё не отвечали на сам пост ──
                for tweet in tweets:
                    if self._stop_event.is_set():
                        break
                    # В режиме hybrid: пропускаем только если УЖЕ ответили на сам пост
                    # (комментарий к нему будет сделан в режиме B — это нормально)
                    already_post = await was_already_replied(
                        self.account_id, tweet.id, tweet.id
                    )
                    if not already_post:
                        chosen_tweet = tweet
                        chosen_comment = None
                        logger.info(
                            f"[Worker:{self.account_id}] 📝 Режим A: @{tweet.author_username} → на пост"
                        )
                        break
                    else:
                        logger.debug(
                            f"[Worker:{self.account_id}] Пост {tweet.id} — уже ответили на пост — пропуск"
                        )
                if not chosen_tweet:
                    logger.info(
                        f"[Worker:{self.account_id}] Режим A: нет новых постов → переключаем на коммент"
                    )
                    mode_reply = "comment"

            if mode_reply == "comment":
                # ── Режим B: ответ на топ-комментарий поста ─────────────────
                # Приоритет: сначала ищем пост на который мы УЖЕ ответили постом
                # но ещё не ответили на комментарий — это и есть пара "Пост+Комент"
                candidate_tweets = []
                for tweet in tweets:
