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
                ),
            ],
            [
                InlineKeyboardButton(
                    f"{'✅ ' if ai_prov == 'openai' else ''}OpenAI",
                    callback_data=f"set_ai:{acc_id}:openai",
                ),
                InlineKeyboardButton(
                    f"{'✅ ' if ai_prov == 'gemini' else ''}Gemini",
                    callback_data=f"set_ai:{acc_id}:gemini",
                ),
                InlineKeyboardButton(
                    f"{'✅ ' if ai_prov == 'groq' else ''}Groq",
                    callback_data=f"set_ai:{acc_id}:groq",
                ),
                InlineKeyboardButton(
                    f"{'✅ ' if ai_prov == 'perplexity' else ''}Perplexity",
                    callback_data=f"set_ai:{acc_id}:perplexity",
                ),
            ],
            [
                InlineKeyboardButton(
                    f"{'✅ ' if sort_by == 'likes' else ''}Топ ❤️",
                    callback_data=f"set_sort:{acc_id}:likes",
                ),
                InlineKeyboardButton(
                    f"{'✅ ' if sort_by == 'views' else ''}Топ 👁",
                    callback_data=f"set_sort:{acc_id}:views",
                ),
            ],
            [
                InlineKeyboardButton(
                    f"{'✅ ' if reply_mode == 'hybrid' else ''}🔀 Пост+Коммент",
                    callback_data=f"set_rmode:{acc_id}:hybrid",
                ),
                InlineKeyboardButton(
                    f"{'✅ ' if reply_mode == 'post_only' else ''}📝 Только посты",
                    callback_data=f"set_rmode:{acc_id}:post_only",
                ),
            ],
            [
                InlineKeyboardButton(
                    "🔑 Ключевые слова", callback_data=f"acc:keywords:{acc_id}"
                )
            ],
            [
                InlineKeyboardButton(
                    "🗑 Удалить аккаунт", callback_data=f"acc:del:{acc_id}"
                ),
                InlineKeyboardButton("🔙 Назад", callback_data="menu:main"),
            ],
        ]
    )
    await query.edit_message_text(text, parse_mode="Markdown", reply_markup=markup)


# ─────────────────────────────────────────────
# ДОБАВЛЕНИЕ АККАУНТА
# ─────────────────────────────────────────────


async def _acc_add_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    ctx.user_data.clear()
    await query.edit_message_text(
        "➕ *Добавление аккаунта X*\n\n"
        "Шаг 1/3 — Отправь <code>auth_token</code>\n\n"
        "1. Открой x.com в Chrome\n"
        "2. F12 → Application → Cookies → x.com\n"
        "3. Скопируй значение <code>auth_token</code>",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(
            [[InlineKeyboardButton("❌ Отмена", callback_data="acc:add_cancel")]]
        ),
    )
    return ST_ADD_TOKEN


async def _acc_add_token(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    token = update.message.text.strip()
    try:
        await update.message.delete()
    except Exception:
        pass
    if len(token) < 20:
        await update.message.reply_text("❌ auth_token слишком короткий.")
        return ST_ADD_TOKEN
    ctx.user_data["auth_token"] = token
    await update.message.reply_text(
        "✅ auth_token получен.\n\nШаг 2/3 — Отправь <code>ct0</code>",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(
            [[InlineKeyboardButton("❌ Отмена", callback_data="acc:add_cancel")]]
        ),
    )
    return ST_ADD_CT0


async def _acc_add_ct0(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    ct0 = update.message.text.strip()
    try:
        await update.message.delete()
    except Exception:
        pass
    if len(ct0) < 20:
        await update.message.reply_text("❌ ct0 слишком короткий.")
        return ST_ADD_CT0
    ctx.user_data["ct0"] = ct0
    await update.message.reply_text(
        "✅ ct0 получен.\n\nШаг 3/3 — Прокси (необязательно)\n"
        "Формат: <code>http://user:pass@host:port</code>",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(
            [
                [InlineKeyboardButton("➡️ Пропустить", callback_data="acc:add_noproxy")],
                [InlineKeyboardButton("❌ Отмена", callback_data="acc:add_cancel")],
            ]
        ),
    )
    return ST_ADD_PROXY


async def _acc_add_proxy_text(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    ctx.user_data["proxy_url"] = update.message.text.strip()
    return await _acc_save(update.message, ctx, edit=False)


async def _acc_add_noproxy(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    ctx.user_data["proxy_url"] = None
    return await _acc_save(query.message, ctx, edit=True)


async def _acc_save(message, ctx: ContextTypes.DEFAULT_TYPE, edit: bool = False) -> int:
    from config import encrypt
    from db import add_proxy
    from proxy import proxy_manager
    from twitter import TwitterClient

    auth_token = ctx.user_data.get("auth_token", "")
    ct0 = ctx.user_data.get("ct0", "")
    proxy_url = ctx.user_data.get("proxy_url")

    info_text = "⏳ Проверяем сессию..."
    if edit:
        await message.edit_text(info_text)
    else:
        await message.reply_text(info_text)

    proxy_id = None
    if proxy_url:
        ptype = "socks5" if proxy_url.startswith("socks") else "http"
        try:
            proxy_id = await add_proxy(proxy_url, ptype)
            await proxy_manager.reload()
        except Exception as e:
            logger.warning(f"Прокси не сохранён: {e}")

    auth_enc = encrypt(auth_token)
    ct0_enc = encrypt(ct0)
    proxies = await get_proxies() if proxy_id else []
    proxy = (
        next((p for p in proxies if p["id"] == proxy_id), None) if proxy_id else None
    )
    username = None
    client = TwitterClient(
        account_id=0, auth_token_enc=auth_enc, ct0_enc=ct0_enc, proxy=proxy
    )
    try:
        await client.__aenter__()
        username = await client.verify_session()
    except Exception as e:
        logger.error(f"Проверка сессии: {e}")
    finally:
        await client.close()

    if not username:
        fail_text = "❌ *Сессия недействительна*\n\nПроверь auth\\_token и ct0."
        if edit:
            await message.edit_text(
                fail_text, parse_mode="Markdown", reply_markup=_back()
            )
        else:
            await message.reply_text(
                fail_text, parse_mode="Markdown", reply_markup=_back()
            )
        ctx.user_data.clear()
        return ConversationHandler.END

    try:
        acc_id = await add_account(username, auth_enc, ct0_enc, proxy_id)
    except Exception as e:
        err = f"❌ Ошибка сохранения: {e}"
        if edit:
            await message.edit_text(err, reply_markup=_back())
        else:
            await message.reply_text(err, reply_markup=_back())
        ctx.user_data.clear()
        return ConversationHandler.END

    ok_text = f"✅ *Аккаунт @{username} добавлен!*\n\nПрокси: `{proxy_url or 'нет'}`"
    markup = InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    f"⚙️ Настройки @{username}", callback_data=f"settings:{acc_id}"
                )
            ],
            [InlineKeyboardButton("🔙 Главное меню", callback_data="menu:main")],
        ]
    )
    if edit:
        await message.edit_text(ok_text, parse_mode="Markdown", reply_markup=markup)
    else:
        await message.reply_text(ok_text, parse_mode="Markdown", reply_markup=markup)
    ctx.user_data.clear()
    return ConversationHandler.END


async def _acc_add_cancel(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    ctx.user_data.clear()
    await query.edit_message_text("❌ Добавление отменено.", reply_markup=_back())
    return ConversationHandler.END


# ─────────────────────────────────────────────
# УДАЛЕНИЕ АККАУНТА
# ─────────────────────────────────────────────


async def _show_delete_list(query) -> None:
    accounts = await get_accounts(active_only=False)
    if not accounts:
        await query.edit_message_text("Аккаунтов нет.", reply_markup=_back())
        return
    buttons = [
        [
            InlineKeyboardButton(
                f"🗑 @{a['username']}", callback_data=f"acc:del:{a['id']}"
            )
        ]
