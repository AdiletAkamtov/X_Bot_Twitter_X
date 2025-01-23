"""

tg_bot.py — Управление ботом через Telegram (минимальная версия)

"""



from __future__ import annotations



import random

import warnings

from datetime import datetime, timezone

from typing import Optional



from telegram.warnings import PTBUserWarning



warnings.filterwarnings("ignore", category=PTBUserWarning)

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update

from telegram.ext import (

    Application,

    CallbackQueryHandler,

    CommandHandler,

    ContextTypes,

    ConversationHandler,

    MessageHandler,

    filters,

)



import state

from config import BotDefaults, get_settings, human_delay, logger

from db import (

    add_account,

    delete_account,

    get_account,

    get_accounts,

    get_all_settings,

    get_daily_count,

    get_keywords,

    get_proxies,

    set_keywords,

    set_setting,

    update_log_status,

)



(ST_ADD_TOKEN, ST_ADD_CT0, ST_ADD_PROXY, ST_SET_KEYWORDS) = range(4)



import re as _re



_action_in_progress: set[int] = set()





def _lock_user(user_id: int) -> bool:

    if user_id in _action_in_progress:

        return False

    _action_in_progress.add(user_id)

    return True





def _unlock_user(user_id: int) -> None:

    _action_in_progress.discard(user_id)





async def _is_admin(update: Update) -> bool:

    uid = update.effective_user.id if update.effective_user else None

    return uid in get_settings().telegram_admin_ids





def _back(target: str = "menu:main") -> InlineKeyboardMarkup:

    return InlineKeyboardMarkup(

        [[InlineKeyboardButton("🔙 Назад", callback_data=target)]]

    )





def _md_escape(text: str) -> str:

    return _re.sub(r"([_*`\[])", r"\\\1", str(text))





def _human_age(created_at_str: str) -> str:

    if not created_at_str:

        return "неизвестно"

    try:

        dt = datetime.strptime(created_at_str, "%a %b %d %H:%M:%S +0000 %Y")

        dt = dt.replace(tzinfo=timezone.utc)

        minutes = int((datetime.now(timezone.utc) - dt).total_seconds() // 60)

        if minutes < 1:

            return "только что"

        if minutes < 60:

            return f"{minutes} мин назад"

        if minutes < 1440:

            return f"{minutes // 60} ч назад"

        return f"{minutes // 1440} дн назад"

    except Exception:

        return "неизвестно"





def _find_pending(log_id: int) -> Optional[dict]:

    for items in state.pending_queue.values():

        for item in items:

            if item["log_id"] == log_id:

                return item

    return None





def _remove_pending(acc_id: int, log_id: int) -> None:

    queue = state.pending_queue.get(acc_id, [])

    state.pending_queue[acc_id] = [i for i in queue if i["log_id"] != log_id]





_hitl_store: dict[int, dict] = {}





def _register_hitl_item(log_id: int, item: dict) -> None:

    _hitl_store[log_id] = item





def _pop_hitl_item(log_id: int) -> Optional[dict]:

    return _hitl_store.pop(log_id, None)





# ─────────────────────────────────────────────

# ГЛАВНОЕ МЕНЮ

# ─────────────────────────────────────────────





async def _show_main_menu(query_or_message, edit: bool = True) -> None:

    accounts = await get_accounts(active_only=False)

    lines = ["🤖 *X AutoReply Bot*\n"]

    for a in accounts:

        running = (

            state.worker_manager.is_running(a["id"]) if state.worker_manager else False

        )

        today = await get_daily_count(a["id"])

        st = await get_all_settings(a["id"])

        auto = st.get("auto_publish", False)

        icon = "🟢" if running else "🔴"

        pub = "🚀 авто" if auto else "✋ ручной"

        lines.append(f"{icon} @{a['username']} | {pub} | сегодня: {today}")

    text = (

        "\n".join(lines) if len(lines) > 1 else "🤖 *X AutoReply Bot*\n\nАккаунтов нет."

    )

    buttons = []

    for a in accounts:

        running = (

            state.worker_manager.is_running(a["id"]) if state.worker_manager else False

        )

        buttons.append(

            [

                InlineKeyboardButton(

                    f"{'⏹ Стоп' if running else '▶️ Старт'} @{a['username']}",

                    callback_data=f"{'stop' if running else 'start'}:{a['id']}",

                ),

                InlineKeyboardButton("🧪 Тест", callback_data=f"test:{a['id']}"),

                InlineKeyboardButton(

                    "⚙️ Настройки", callback_data=f"settings:{a['id']}"

                ),

            ]

        )

    buttons.append(

        [

            InlineKeyboardButton("➕ Добавить аккаунт", callback_data="acc:add"),

            InlineKeyboardButton("🗑 Удалить", callback_data="acc:del_list"),

        ]

    )

    buttons.append([InlineKeyboardButton("🔄 Обновить", callback_data="menu:main")])

    markup = InlineKeyboardMarkup(buttons)

    if edit:

        try:

            await query_or_message.edit_message_text(

                text, parse_mode="Markdown", reply_markup=markup

            )

            return

        except Exception:

            pass

        try:

            await query_or_message.delete_message()

        except Exception:

            pass

        await query_or_message.message.reply_text(

            text, parse_mode="Markdown", reply_markup=markup

        )

    else:

        await query_or_message.reply_text(

            text, parse_mode="Markdown", reply_markup=markup

        )





# ─────────────────────────────────────────────

# НАСТРОЙКИ (только переключатели)

# ─────────────────────────────────────────────





async def _show_settings(acc_id: int, query) -> None:

    acc = await get_account(acc_id)

    if not acc:

        await query.edit_message_text("❌ Аккаунт не найден.", reply_markup=_back())

        return

    st = await get_all_settings(acc_id)

    mode = st.get("search_mode", BotDefaults.search_mode)

    min_likes = st.get("min_likes", BotDefaults.min_likes)

    max_age = st.get("max_age_min", BotDefaults.max_post_age_minutes)

    sort_by = st.get("comment_sort", BotDefaults.comment_sort)

    reply_mode = st.get("reply_mode", "hybrid")

    auto_pub = st.get("auto_publish", BotDefaults.auto_publish)

    min_d = int(st.get("min_delay", BotDefaults.min_delay_seconds))

    daily_lim = st.get("daily_limit", BotDefaults.daily_comment_limit)

    ai_prov = st.get("ai_provider", get_settings().default_ai_provider)

    keywords = await get_keywords(acc_id)

    kw_str = (

        ", ".join(keywords[:3]) + ("..." if len(keywords) > 3 else "")

        if keywords

        else "не заданы"

    )



    text = (

        f"⚙️ *Настройки* @{acc['username']}\n\n"

        f"🔍 Режим: `{mode}`\n"

        f"❤️ Мин. лайков: `{min_likes}`\n"

        f"⏰ Макс. возраст: `{max_age}` мин\n"

        f"📝 Ответ: `{'пост + коммент' if reply_mode == 'hybrid' else 'только посты'}`\n"

        f"🚀 Публикация: `{'авто ✅' if auto_pub else 'ручной ✋'}`\n"

        f"⏱ Задержка: `{min_d // 60}` мин\n"

        f"📊 Лимит/день: `{daily_lim}`\n"

        f"🤖 AI: `{ai_prov}`\n"

        f"🔑 Слова: `{kw_str}`\n"

    )

    markup = InlineKeyboardMarkup(

        [

            [

                InlineKeyboardButton(

                    f"{'✅ ' if auto_pub else ''}🚀 Авто",

                    callback_data=f"set_auto:{acc_id}:1",

                ),

                InlineKeyboardButton(

                    f"{'✅ ' if not auto_pub else ''}✋ Ручной",

                    callback_data=f"set_auto:{acc_id}:0",

                ),

            ],

            [

                InlineKeyboardButton(

                    f"{'✅ ' if mode == 'keywords' else ''}🔍 Слова",

                    callback_data=f"set_mode:{acc_id}:keywords",

                ),

                InlineKeyboardButton(

                    f"{'✅ ' if mode == 'list' else ''}📋 Списки",

                    callback_data=f"set_mode:{acc_id}:list",

                ),

                InlineKeyboardButton(

                    f"{'✅ ' if mode == 'recommendations' else ''}✨ Рек.",

                    callback_data=f"set_mode:{acc_id}:recommendations",
        for a in accounts
    ]
    buttons.append([InlineKeyboardButton("🔙 Назад", callback_data="menu:main")])
    await query.edit_message_text(
        "🗑 *Выбери аккаунт для удаления:*",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(buttons),
    )


async def _acc_delete_confirm(acc_id: int, query) -> None:
    acc = await get_account(acc_id)
    if not acc:
        await query.edit_message_text("❌ Аккаунт не найден.", reply_markup=_back())
        return
    await query.edit_message_text(
        f"⚠️ *Удалить @{acc['username']}?*",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(
            [
                [
                    InlineKeyboardButton(
                        "🗑 Да, удалить", callback_data=f"acc:del_ok:{acc_id}"
                    )
                ],
                [InlineKeyboardButton("❌ Отмена", callback_data="menu:main")],
            ]
        ),
    )


async def _acc_delete_ok(acc_id: int, query) -> None:
    acc = await get_account(acc_id)
    username = acc["username"] if acc else str(acc_id)
    try:
        if state.worker_manager:
            await state.worker_manager.stop(acc_id)
    except Exception:
        pass
    await delete_account(acc_id)
    await query.edit_message_text(
        f"✅ Аккаунт @{username} удалён.", reply_markup=_back()
    )


# ─────────────────────────────────────────────
# КЛЮЧЕВЫЕ СЛОВА
# ─────────────────────────────────────────────


async def _show_keywords(acc_id: int, query) -> None:
    acc = await get_account(acc_id)
    keywords = await get_keywords(acc_id)
    kw_text = "\n".join(f"• `{kw}`" for kw in keywords) if keywords else "_не заданы_"
    await query.edit_message_text(
        f"🔑 *Ключевые слова* @{acc['username']}\n\n{kw_text}\n\n"
        "📌 Форматы:\n"
        "• Простой: `bitcoin, crypto, web3`\n"
        "• Raw: `(finance OR markets) min_faves:10 -filter:replies lang:en`",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(
            [
                [InlineKeyboardButton("✏️ Изменить", callback_data=f"kw:edit:{acc_id}")],
                [InlineKeyboardButton("🔙 Назад", callback_data=f"settings:{acc_id}")],
            ]
        ),
    )


async def _kw_edit_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    acc_id = int(query.data.split(":")[2])
    ctx.user_data["kw_acc_id"] = acc_id
    acc = await get_account(acc_id)
    await query.edit_message_text(
        f"✏️ *Ключевые слова* @{acc['username']}\n\nОтправь слова через запятую или raw-запрос:",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(
            [
                [
                    InlineKeyboardButton(
                        "❌ Отмена", callback_data=f"acc:keywords:{acc_id}"
                    )
                ]
            ]
        ),
    )
    return ST_SET_KEYWORDS


async def _kw_save(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    acc_id = ctx.user_data.get("kw_acc_id")
    text = update.message.text.strip()
    _RAW = (
        "min_faves:",
        "min_retweets:",
        "since:",
        "until:",
        "-filter:",
        "from:",
        " OR ",
        " AND ",
    )
    is_raw = any(s in text for s in _RAW)
    words = [text] if is_raw else [w.strip() for w in text.split(",") if w.strip()]
    if not words:
        await update.message.reply_text("❌ Пусто. Попробуй снова.")
        return ST_SET_KEYWORDS
    await set_keywords(acc_id, words)
    acc = await get_account(acc_id)
    await update.message.reply_text(
        f"✅ {'Raw-запрос' if is_raw else f'{len(words)} слов'} сохранено для @{acc['username']}",
        reply_markup=InlineKeyboardMarkup(
            [[InlineKeyboardButton("⚙️ Настройки", callback_data=f"settings:{acc_id}")]]
        ),
    )
    ctx.user_data.clear()
    return ConversationHandler.END


# ─────────────────────────────────────────────
# HITL: публикация / лайк / скип / регенерация
# ─────────────────────────────────────────────


async def _handle_post(log_id: int, action: str, query, ctx) -> None:
    item = _find_pending(log_id)
    if not item:
        await query.edit_message_text("⚠️ Запрос устарел или уже обработан.")
        return

    reply_text = (
        item["reply_text"]
        if action == "post1"
        else item.get("reply_variant2", item["reply_text"])
    )
    acc_id = item["account_id"]
    acc_name = item.get("account_name", f"id={acc_id}")
    post_url = item.get("post_url", "")
    tweet = item.get("tweet")
    comment = item.get("comment")
    target_id = item.get("target_id") or (
        comment.id if comment else (tweet.id if tweet else None)
    )
    tweet_id = tweet.id if tweet else None
    comment_link = (
        f"\n[Комментарий](https://x.com/i/status/{comment.id})" if comment else ""
    )

    _posting_lock = None
    try:
        if state.worker_manager:
            _worker = state.worker_manager._workers.get(acc_id)
        else:
            _worker = None
        if _worker:
            _posting_lock = _worker[0]._posting_lock
            await _posting_lock.acquire()
    except Exception:
        _posting_lock = None

    from config import compose_delay, read_delay
    from proxy import proxy_manager as _pm
    from twitter import TwitterClient as _TC

    acc = await get_account(acc_id)
    if not acc:
        await query.edit_message_text("❌ Аккаунт не найден.")
        if _posting_lock and _posting_lock.locked():
            _posting_lock.release()
        return

    proxy = await _pm.get_proxy_for_account(acc.get("proxy_id"))
    await query.edit_message_text("⏳ *Публикуем...*", parse_mode="Markdown")
    if tweet:
        await read_delay(tweet.text)
    await human_delay()  # randomized anti-detect pause
    await compose_delay(reply_text)

    def _release():
        if _posting_lock and _posting_lock.locked():
            _posting_lock.release()

    try:
        async with _TC(
            account_id=acc_id,
            auth_token_enc=acc["auth_token"],
            ct0_enc=acc["ct0"],
            proxy=proxy,
        ) as client:
            new_id = (
                await client.post_reply(reply_text, target_id, tweet_url=post_url)
                if target_id
                else None
            )
            if new_id and tweet_id:
                try:
                    await client.like_tweet(tweet_id)
                except Exception:
                    pass
    except Exception as e:
        logger.error(f"[TG:post] @{acc_name}: {e}")
        _release()
        await update_log_status(log_id, "error")
        await query.edit_message_text("❌ Ошибка публикации.")
        return

    _release()

    if new_id:
        await update_log_status(log_id, "posted")
        from config import rate_limiter
        from db import increment_daily_count, update_account_last_used

        await increment_daily_count(acc_id)
        rate_limiter.record(acc_id)
        await update_account_last_used(acc_id)
        _remove_pending(acc_id, log_id)
        _pop_hitl_item(log_id)
        logger.success(f"[TG:post] @{acc_name} → {new_id}")
        await query.edit_message_text(
            f"✅ *Опубликовано!*\n\n"
            f"[Посмотреть ответ](https://x.com/i/status/{new_id}){comment_link}\n"
            f"[Открыть пост]({post_url})",
            parse_mode="Markdown",
            disable_web_page_preview=True,
            reply_markup=InlineKeyboardMarkup(
                [[InlineKeyboardButton("🏠 Меню", callback_data="menu:main")]]
            ),
        )
    else:
        await update_log_status(log_id, "skipped")
        _remove_pending(acc_id, log_id)
        _pop_hitl_item(log_id)
        await query.edit_message_text(
            "⚠️ Не удалось опубликовать — X ограничил ответ.",
            reply_markup=InlineKeyboardMarkup(
                [
                    [
                        InlineKeyboardButton(
                            "🔄 Следующий", callback_data=f"test:{acc_id}"
                        )
                    ],
                    [InlineKeyboardButton("🏠 Меню", callback_data="menu:main")],
                ]
            ),
        )


async def _handle_like_only(log_id: int, query, ctx) -> None:
    item = _find_pending(log_id) or _hitl_store.get(log_id)
    if not item:
        await query.edit_message_text("⚠️ Запрос устарел.")
        return
    tweet = item.get("tweet")
    tweet_id = tweet.id if tweet else None
    if not tweet_id:
        await query.edit_message_text("⚠️ Нет ID твита.")
        return

    await query.edit_message_text("⏳ Ставим лайк...", reply_markup=None)
    try:
        from proxy import proxy_manager as _pm
        from twitter import TwitterClient as _TC

        acc = await get_account(item["account_id"])
        proxy = await _pm.get_proxy_for_account(acc.get("proxy_id")) if acc else None
        async with _TC(
            account_id=item["account_id"],
            auth_token_enc=acc["auth_token"],
            ct0_enc=acc["ct0"],
            proxy=proxy,
        ) as client:
            success = await client.like_tweet(tweet_id)
        if success:
            await update_log_status(log_id, "liked_only")
            _remove_pending(item["account_id"], log_id)
            _pop_hitl_item(log_id)
            await query.edit_message_text(
                "❤️ Лайк поставлен.",
                reply_markup=InlineKeyboardMarkup(
                    [[InlineKeyboardButton("🏠 Меню", callback_data="menu:main")]]
                ),
            )
        else:
            await query.edit_message_text(
                "⚠️ Лайк не удался.",
                reply_markup=InlineKeyboardMarkup(
                    [[InlineKeyboardButton("🏠 Меню", callback_data="menu:main")]]
                ),
            )
    except Exception as e:
        logger.error(f"[TG:like] {e}")
        await query.edit_message_text(f"❌ Ошибка: {str(e)[:200]}")


async def _handle_regen(log_id: int, query, ctx) -> None:
    item = _find_pending(log_id)
    if not item:
        await query.edit_message_text("⚠️ Запрос устарел.")
        return
    await query.edit_message_text("🔄 Генерируем...")
    from ai import REPLY_SKIP as _SKIP
    from ai import generate_reply

    try:
        st = await get_all_settings(item["account_id"])
        prompt = st.get("system_prompt", BotDefaults.system_prompt)
        prov = st.get("ai_provider", None)
        r1 = r2 = _SKIP
        for _ in range(3):
            r1, prov = await generate_reply(
                post_text=item["tweet"].text,
                comment_text=item["comment"].text if item.get("comment") else None,
                provider=prov,
                system_prompt=prompt,
            )
            r2, _ = await generate_reply(
                post_text=item["tweet"].text,
                comment_text=item["comment"].text if item.get("comment") else None,
                provider=prov,
                system_prompt=prompt,
            )
            if r1 != _SKIP and r2 != _SKIP:
                break
        if r1 == _SKIP or r2 == _SKIP:
            await query.edit_message_text(
                "🤖 AI пропускает этот пост.",
                reply_markup=InlineKeyboardMarkup(
                    [[InlineKeyboardButton("❌ Скип", callback_data=f"skip:{log_id}")]]
                ),
            )
            return
    except Exception as e:
        await query.edit_message_text(f"❌ Ошибка: {e}")
        return

    item["reply_text"] = r1
    item["reply_variant2"] = r2
    item["provider"] = prov
    from db import execute

    await execute(
        "UPDATE posts_log SET reply_text=?, reply_variant2=? WHERE id=?",
        (r1, r2, log_id),
    )
    tweet = item["tweet"]
    post_url = item.get("post_url", "")
    await query.edit_message_text(
        f"🔄 *Перегенерировано* `[{prov}]`\n\n"
        f"@{_md_escape(tweet.author_username)}: {_md_escape(tweet.text[:150])}\n"
        f"[Открыть пост]({post_url})\n\n"
        f"*Вариант 1:*\n{_md_escape(r1)}\n\n"
        f"*Вариант 2:*\n{_md_escape(r2)}",
        parse_mode="Markdown",
        disable_web_page_preview=True,
        reply_markup=InlineKeyboardMarkup(
            [
                [
                    InlineKeyboardButton(
                        "✅ Вариант 1", callback_data=f"post1:{log_id}"
                    ),
                    InlineKeyboardButton(
                        "✅ Вариант 2", callback_data=f"post2:{log_id}"
                    ),
                ],
                [
                    InlineKeyboardButton("🔄 Ещё раз", callback_data=f"regen:{log_id}"),
                    InlineKeyboardButton("❤️ Лайк", callback_data=f"likeonly:{log_id}"),
                    InlineKeyboardButton("❌ Скип", callback_data=f"skip:{log_id}"),
                ],
            ]
        ),
    )


# ─────────────────────────────────────────────
# ТЕСТ
# ─────────────────────────────────────────────


async def _handle_test(acc_id: int, query) -> None:
    from ai import REPLY_SKIP as _SKIP
    from ai import generate_reply
    from config import compose_delay, read_delay
    from db import log_post, was_replied_any
    from proxy import proxy_manager
    from twitter import TwitterClient

    await query.edit_message_text("🧪 Запускаем тест...")
    acc = await get_account(acc_id)
    if not acc:
        await query.edit_message_text("❌ Аккаунт не найден.", reply_markup=_back())
        return

    st = await get_all_settings(acc_id)
    mode = st.get("search_mode", BotDefaults.search_mode)
    min_likes = st.get("min_likes", BotDefaults.min_likes)
    min_rt = st.get("min_retweets", BotDefaults.min_retweets)
    max_age = st.get("max_age_min", BotDefaults.max_post_age_minutes)
    sort_by = st.get("comment_sort", BotDefaults.comment_sort)
    auto_publish = st.get("auto_publish", BotDefaults.auto_publish)
    system_prompt = st.get("system_prompt", BotDefaults.system_prompt)
    ai_provider = st.get("ai_provider", None)
    reply_mode = st.get("reply_mode", "hybrid")

    proxy = await proxy_manager.get_proxy_for_account(acc.get("proxy_id"))
    client = TwitterClient(
        account_id=acc_id,
        auth_token_enc=acc["auth_token"],
        ct0_enc=acc["ct0"],
        proxy=proxy,
    )
    try:
        await client.__aenter__()
        username = await client.verify_session()
        if not username:
            await query.edit_message_text(
                "❌ Сессия недействительна.", reply_markup=_back()
            )
            return

        await query.edit_message_text(f"🧪 @{username} ✅\n🔍 Ищем посты ({mode})...")

        tweets = []
        if mode == "keywords":
            kws = await get_keywords(acc_id)
            if not kws:
                await query.edit_message_text(
                    "⚠️ Ключевые слова не заданы.", reply_markup=_back()
                )
                return
            kw = random.choice(kws)
            tweets = await client.search_tweets(
                kw,
                min_likes=min_likes,
                min_retweets=min_rt,
                max_age_minutes=max_age,
                limit=10,
            )
        elif mode == "list":
            from db import get_x_lists

            urls = await get_x_lists(acc_id)
            if not urls:
                await query.edit_message_text(
                    "⚠️ Списки X не заданы.", reply_markup=_back()
                )
                return
            for url in urls[:3]:
                t = await client.get_list_tweets(url, min_likes=min_likes, limit=10)
                tweets.extend(t)
                if tweets:
                    break
        else:
            tweets = await client.get_recommended_tweets(min_likes=min_likes, limit=10)

        if not tweets:
            await query.edit_message_text(
                "⚠️ Постов не найдено. Снизь мин. лайки.", reply_markup=_back()
            )
            return

        fresh = [t for t in tweets[:10] if not await was_replied_any(acc_id, t.id)]
        if not fresh:
            await query.edit_message_text(
                "⚠️ На все найденные посты уже ответили.", reply_markup=_back()
            )
            return

        candidates = (
            list(fresh[:10]) if mode == "keywords" else [random.choice(fresh[:5])]
        )
        random.shuffle(candidates)

        tweet = comment = reply_text = prov = None
        skipped = 0

        for _cand in candidates:
            tweet = _cand
            post_url = f"https://x.com/{tweet.author_username}/status/{tweet.id}"
            comment = None
            if reply_mode == "hybrid" and random.random() < 0.5:
                comment = await client.get_top_comment(tweet, sort_by=sort_by)
            await query.edit_message_text(
                f"🧪 @{username}\n✅ Постов: {len(fresh)}\n"
                f"{'💬 коммент' if comment else '📝 пост'} | пропущено: {skipped}\n⏳ AI..."
            )
            await read_delay(tweet.text)
            reply_text, prov = await generate_reply(
