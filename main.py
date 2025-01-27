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
                    already_post = await was_already_replied(
                        self.account_id, tweet.id, tweet.id
                    )
                    if already_post:
                        candidate_tweets.insert(
                            0, tweet
                        )  # приоритет: уже ответили на пост
                    else:
                        candidate_tweets.append(tweet)

                for tweet in candidate_tweets:
                    if self._stop_event.is_set():
                        break
                    await asyncio.sleep(random.uniform(1.5, 3.5))
                    comment = await client.get_top_comment(
                        tweet, sort_by=sort_by, own_username=username
                    )
                    if not comment:
                        logger.debug(
                            f"[Worker:{self.account_id}] {tweet.id}: нет комментариев — пропуск"
                        )
                        continue
                    if await was_already_replied(self.account_id, tweet.id, comment.id):
                        logger.debug(
                            f"[Worker:{self.account_id}] {tweet.id}: коммент {comment.id} уже обработан"
                        )
                        continue
                    chosen_tweet = tweet
                    chosen_comment = comment
                    logger.info(
                        f"[Worker:{self.account_id}] 💬 Режим B: @{tweet.author_username} "
                        f"→ коммент @{comment.author_username}: {comment.text[:80]}"
                    )
                    break

            if not chosen_tweet:
                logger.info(
                    f"[Worker:{self.account_id}] Нет подходящих постов в этой выборке"
                )
                return

            post_url = (
                f"https://x.com/{chosen_tweet.author_username}/status/{chosen_tweet.id}"
            )

            # ── Если режим B не нашёл ни одного комментария — фолбэк на пост ──
            if mode_reply == "comment" and not chosen_comment:
                logger.info(
                    f"[Worker:{self.account_id}] 💬→📝 Нет комментариев ни у одного поста → фолбэк на ответ на пост"
                )
                # Найти пост на который не отвечали вообще (ни на сам пост, ни на комментарии)
                for tweet in tweets:
                    if not await was_replied_any(self.account_id, tweet.id):
                        chosen_tweet = tweet
                        chosen_comment = None
                        post_url = f"https://x.com/{chosen_tweet.author_username}/status/{chosen_tweet.id}"
                        break

            if not chosen_tweet:
                logger.info(
                    f"[Worker:{self.account_id}] Нет подходящих постов после фолбэка"
                )
                return

            # ── Определяем target ДО генерации AI ────────────────────────────
            target_id = chosen_comment.id if chosen_comment else chosen_tweet.id
            log_comment_id = target_id
            log_comment_text = (
                chosen_comment.text if chosen_comment else chosen_tweet.text
            )

            reply_target = (
                f"💬 на комментарий @{chosen_comment.author_username}"
                if chosen_comment
                else f"📝 на пост @{chosen_tweet.author_username}"
            )
            logger.info(f"[Worker:{self.account_id}] 🎯 Цель: {reply_target}")

            # ── AI генерирует ЗНАЯ точную цель ───────────────────────────────
            logger.info(
                f"[Worker:{self.account_id}] 🤖 AI ({ai_provider}) | {reply_target}..."
            )
            await read_delay(chosen_tweet.text)
            try:
                reply_text, provider_used = await generate_reply(
                    post_text=chosen_tweet.text,
                    comment_text=chosen_comment.text if chosen_comment else None,
                    provider=ai_provider,
                    system_prompt=system_prompt,
                )

                # ── Пост не по теме — AI вернул SKIP → пропускаем без лога ──
                from ai import REPLY_SKIP as _REPLY_SKIP

                if reply_text == _REPLY_SKIP:
                    logger.info(
                        f"[Worker:{self.account_id}] 🚫 AI SKIP — пост @{chosen_tweet.author_username} не по теме"
                    )
                    return

                # ── Убираем @mention в начале если AI добавил сам ────────────
                # X автоматически добавляет @mention при reply — двойной mention выглядит плохо
                import re as _re

                reply_text = _re.sub(r"^@\w+\s*", "", reply_text).strip()

                logger.info(
                    f"[Worker:{self.account_id}] 💬 Reply ({reply_target}): {reply_text[:100]}..."
                )
            except Exception as e:
                logger.error(f"[Worker:{self.account_id}] AI failed: {e}")
                return

            log_id = await log_post(
                account_id=self.account_id,
                post_id=chosen_tweet.id,
                post_url=post_url,
                post_text=chosen_tweet.text,
                comment_id=log_comment_id,
                comment_text=log_comment_text,
                reply_text=reply_text,
                reply_variant2="",
                ai_provider=provider_used,
            )

            # ── Публикуем или отправляем в Telegram ──────────────────────────
            if auto_publish:
                await compose_delay(reply_text)
                new_id = await client.post_reply(
                    reply_text, target_id, tweet_url=post_url
                )
                if new_id:
                    await update_log_status(log_id, "posted")
                    await increment_daily_count(self.account_id)
                    rate_limiter.record(self.account_id)
                    await update_account_last_used(self.account_id)
                    if chosen_comment:
                        logger.success(
                            f"[Worker:{self.account_id}] ✅ ОТВЕТ НА КОММЕНТАРИЙ "
                            f"@{chosen_comment.author_username} "
                            f"(пост @{chosen_tweet.author_username}) "
                            f"→ {post_url} | new_id={new_id}"
                        )
                    else:
                        logger.success(
                            f"[Worker:{self.account_id}] ✅ ОТВЕТ НА ПОСТ "
                            f"@{chosen_tweet.author_username} "
                            f"→ {post_url} | new_id={new_id}"
                        )
                    # ── Отправляем уведомление в Telegram ──
                    if state.tg_app:
                        try:
                            await send_posted_notification(
                                app=state.tg_app,
                                account_name=username,
                                tweet=chosen_tweet,
                                comment=chosen_comment,
                                reply_text=reply_text,
                                post_url=post_url,
                                new_tweet_id=new_id,
                                provider=provider_used,
                            )
                        except Exception as _tg_err:
                            logger.warning(
                                f"[Worker:{self.account_id}] TG notify failed: {_tg_err}"
                            )
                elif not new_id:
                    await update_log_status(log_id, "skipped")
                    logger.info(
                        f"[Worker:{self.account_id}] ⚠️ Не удалось опубликовать (цель недоступна)"
                    )
            else:
                item = {
                    "log_id": log_id,
                    "account_id": self.account_id,
                    "account_name": username,
                    "tweet": chosen_tweet,
                    "comment": chosen_comment,
                    "target_id": target_id,
                    "is_second_visit": chosen_comment is not None,
                    "reply_text": reply_text,
                    "reply_variant2": "",
                    "post_url": post_url,
                    "provider": provider_used,
                    "image_urls": chosen_tweet.image_urls or [],
                    # Store credentials instead of live client — client is closed
                    # when _cycle() returns (async with block exits). _handle_post
                    # will create a fresh client when the user presses POST.
                    "auth_token_enc": account["auth_token"],
                    "ct0_enc": account["ct0"],
                    "proxy_id": account.get("proxy_id"),
                }
                _pending_queue.setdefault(self.account_id, []).append(item)
                await _notify_pending(item)
                logger.info(
                    f"[Worker:{self.account_id}] ⏳ Telegram approval "
                    f"({'режим B: коммент' if chosen_comment else 'режим A: пост'})"
                )

    async def _fetch_tweets(self, client: TwitterClient, settings: dict):
        mode = settings.get("search_mode", BotDefaults.search_mode)
        min_likes = _int(settings.get("min_likes"), BotDefaults.min_likes)
        min_rt = _int(settings.get("min_retweets"), BotDefaults.min_retweets)
        max_age = _int(settings.get("max_age_min"), BotDefaults.max_post_age_minutes)
        lang = settings.get("lang_filter", "en")  # default: English only

        logger.debug(
            f"[Worker:{self.account_id}] _fetch_tweets | mode={mode} min_likes={min_likes} min_rt={min_rt} max_age={max_age}min lang={lang}"
        )

        if mode == "keywords":
            keywords = await get_keywords(self.account_id)
            logger.debug(f"[Worker:{self.account_id}] Keywords: {keywords}")
            if not keywords:
                logger.warning(
                    f"[Worker:{self.account_id}] No keywords configured — add keywords in Settings."
                )
                return []

            # Пробуем все ключевые слова по очереди, пока не наберём 30+ новых твитов
            all_tweets: list = []
            shuffled_kws = list(keywords)
            random.shuffle(shuffled_kws)
            for kw in shuffled_kws[:5]:  # max 5 keywords per cycle
                logger.info(f"[Worker:{self.account_id}] Searching keyword: '{kw}'")
                found = await client.search_tweets(
                    query=kw,
                    min_likes=min_likes,
                    min_retweets=min_rt,
                    max_age_minutes=max_age,
                    lang=lang,
                    limit=50,
                )
                logger.info(
                    f"[Worker:{self.account_id}] Search '{kw}' → {len(found)} tweets"
                )
                # Add only tweets not already in list
                existing_ids = {t.id for t in all_tweets}
                all_tweets.extend(t for t in found if t.id not in existing_ids)
                if len(all_tweets) >= 30:
                    break
            tweets = all_tweets
            logger.info(
                f"[Worker:{self.account_id}] Total unique tweets: {len(tweets)}"
            )

            if not tweets:
                logger.warning(
                    f"[Worker:{self.account_id}] Keywords search returned 0 tweets. "
                    f"Query: '{shuffled_kws[0] if shuffled_kws else '?'}' | min_likes={min_likes} min_rt={min_rt} max_age={max_age}min lang={lang}. "
                    f"Check: 1) lower min_likes in Settings, 2) account may lack search access."
                )
                return []
            return tweets

        elif mode == "list":
            lists = await get_x_lists(self.account_id)
            logger.debug(f"[Worker:{self.account_id}] X Lists: {lists}")
            if not lists:
                logger.warning(f"[Worker:{self.account_id}] No X Lists configured")
                return []
            url = random.choice(lists)
            logger.info(f"[Worker:{self.account_id}] Fetching list: {url}")
            tweets = await client.get_list_tweets(
                list_url=url,
                min_likes=min_likes,
                min_retweets=min_rt,
                max_age_minutes=max_age,
                lang=lang,
            )
            logger.info(
                f"[Worker:{self.account_id}] List lang:{lang} → {len(tweets)} tweets after filters"
            )
            return tweets

        elif mode == "recommendations":
            logger.info(
                f"[Worker:{self.account_id}] Fetching recommendations (min_likes={min_likes}, lang={lang})"
            )
            tweets = await client.get_recommended_tweets(min_likes=min_likes, lang=lang)
            logger.info(
                f"[Worker:{self.account_id}] Recommendations → {len(tweets)} tweets"
            )
            return tweets

        logger.error(f"[Worker:{self.account_id}] Unknown search_mode: '{mode}'")
        return []


# ─────────────────────────────────────────────
# WORKER MANAGER
# ─────────────────────────────────────────────


class WorkerManager:
    def __init__(self):
        self._workers: dict[int, tuple[BotWorker, asyncio.Task]] = {}

    async def start(self, account_id: int) -> bool:
        if account_id in self._workers:
            logger.info(f"Worker {account_id} already running")
            return False
        worker = BotWorker(account_id)
        task = asyncio.create_task(worker.run(), name=f"worker-{account_id}")
        self._workers[account_id] = (worker, task)
        logger.info(f"Started worker for account {account_id}")
        return True

    async def stop(self, account_id: int) -> bool:
        if account_id not in self._workers:
            return False
        worker, task = self._workers.pop(account_id)
        worker.stop()
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        logger.info(f"Stopped worker for account {account_id}")
        return True

    def is_running(self, account_id: int) -> bool:
