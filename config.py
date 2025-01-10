"""

config.py - Settings, logging, crypto, anti-detect delays & headers

"""



from __future__ import annotations

import asyncio

import logging

import os

import random

import sys

import time

from pathlib import Path

from typing import Literal, Optional



from cryptography.fernet import Fernet

from loguru import logger

from pydantic import Field, field_validator

from pydantic_settings import BaseSettings, SettingsConfigDict



# ---------------------------------------------------------------------------

# BASE_DIR -- writable user-data folder

#

# When running as a PyInstaller .exe installed to C:\Program Files\,

# the install folder is READ-ONLY for normal users.

# We keep ALL mutable data (db, logs, .env) in %APPDATA%\XBot instead.

# ---------------------------------------------------------------------------

if getattr(sys, "frozen", False):

    _appdata = os.environ.get("APPDATA") or os.path.expanduser("~")

    BASE_DIR = Path(_appdata) / "XBot"

    EXE_DIR = Path(sys.executable).parent

else:

    BASE_DIR = Path(__file__).parent

    EXE_DIR = BASE_DIR



_ENV_FILE = BASE_DIR / ".env"

_DB_DIR   = BASE_DIR / "data"

_LOG_DIR  = BASE_DIR / "logs"



for _d in (BASE_DIR, _DB_DIR, _LOG_DIR):

    _d.mkdir(parents=True, exist_ok=True)





# ---------------------------------------------------------------------------

# AUTO-CREATE .env on first launch

# ---------------------------------------------------------------------------



def _ensure_env_file() -> None:

    if _ENV_FILE.exists():

        return

    key = Fernet.generate_key().decode()

    _ENV_FILE.write_text(

        f"ENCRYPTION_KEY={key}\n"

        "OPENAI_API_KEY=\n"

        "GEMINI_API_KEY=\n"

        "PERPLEXITY_API_KEY=\n"

        "GROQ_API_KEY=\n"

        "TELEGRAM_BOT_TOKEN=\n"

        "TELEGRAM_ADMIN_IDS=\n"

        "DEFAULT_AI_PROVIDER=groq\n",

        encoding="utf-8",

    )





_ensure_env_file()





def save_env_value(key: str, value: str) -> None:

    """Write or update KEY=VALUE in .env. Called by GUI when user saves settings."""

    lines: list[str] = []

    if _ENV_FILE.exists():

        lines = _ENV_FILE.read_text(encoding="utf-8").splitlines()



    new_lines: list[str] = []

    updated = False

    for line in lines:

        if line.startswith(f"{key}=") or line.startswith(f"{key} ="):

            new_lines.append(f"{key}={value}")

            updated = True

        else:

        "No cheerleading, no hedging everything with 'but DYOR'. "

        "You sound like someone who's been burned enough times to stop being cocky, "

        "but still has conviction.\n\n"

        "When you see a post — reply with ONE sharp take. "

        "Something you'd actually type between watching the tape.\n\n"

        "Rules:\n"

        "- English only\n"

        "- 1-2 sentences MAX -- target 120-150 characters total\n"

        "- No hashtags, no emojis, no 'great point', no 'I agree', no 'absolutely'\n"

        "- Casual but sharp -- like texting a trading buddy, not writing a report\n"

        "- Specific is better than vague -- levels, indicators, flow > generic wisdom\n"

        "Good examples:\n"

        "  'VIX term structure still inverted, that's the tell nobody's watching'\n"

        "  'gamma flip at 5200 -- above that dealers are forced buyers all day'\n"

        "  'retail piling in while GEX went negative yesterday, not a great combo'\n"

        "  'BTC dominance breaking out usually means alts get wrecked first'\n"

        "  'been wrong before but this smells like a stop hunt before the real move'\n\n"

        "Bad examples (never do this):\n"

        "  'Great insight! The market dynamics you described are indeed fascinating...'\n"

        "  'I completely agree with your analysis of the current macroeconomic situation.'\n"

        "  'As a professional trader I can confirm that risk management is key #trading'"

    )





_settings: Optional[AppSettings] = None





def get_settings() -> AppSettings:

    global _settings

    if _settings is None:

        _settings = AppSettings()

    return _settings





def reload_settings() -> AppSettings:

    """Force reload settings from .env -- call after saving new API keys."""

    global _settings, _fernet

    _settings = None

    _fernet = None

    return get_settings()





# ---------------------------------------------------------------------------

# LOGGER

# ---------------------------------------------------------------------------



def _setup_logger() -> None:

    try:

        s = get_settings()

        log_level = s.log_level

        log_file = s.log_file

    except Exception:

        log_level = "INFO"

        log_file = _LOG_DIR / "xbot.log"



    log_file.parent.mkdir(parents=True, exist_ok=True)

    logger.remove()



    _TG_NOISE = (

        "telegram._bot",

        "telegram.ext._extbot",

        "telegram.request._baserequest",

        "telegram.request._httpxrequest",

        "httpx",

        "httpcore",

    )



    def _noise_filter(record) -> bool:

        name = record["name"]

        if any(name.startswith(m) for m in _TG_NOISE):

            return record["level"].name not in ("DEBUG", "TRACE")

        return True



    for noisy in ("httpx", "httpcore", "telegram.ext._application",

                  "apscheduler", "telegram.ext._updater"):

        logging.getLogger(noisy).setLevel(logging.WARNING)



    _LEVEL_ICONS = {

        "TRACE":    "· TRACE  ",

    global _fernet
    if _fernet is None:
        _fernet = Fernet(get_settings().encryption_key.encode())
    return _fernet


def encrypt(text: str) -> str:
    return _get_fernet().encrypt(text.encode()).decode()


def decrypt(token: str) -> str:
    return _get_fernet().decrypt(token.encode()).decode()


def generate_key() -> str:
    return Fernet.generate_key().decode()


# ---------------------------------------------------------------------------
# ANTI-DETECT: delays & headers
# ---------------------------------------------------------------------------

_CHROME_UAS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/127.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_6_1) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/127.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36 Edg/126.0.0.0",
]

_SEC_CH_UA_MAP = {
    "Chrome/128": '"Chromium";v="128", "Not;A=Brand";v="24", "Google Chrome";v="128"',
    "Chrome/127": '"Chromium";v="127", "Not;A=Brand";v="24", "Google Chrome";v="127"',
    "Chrome/126": '"Chromium";v="126", "Not;A=Brand";v="24", "Microsoft Edge";v="126"',
}


def get_browser_headers(ua: Optional[str] = None) -> dict[str, str]:
    if ua is None:
        ua = random.choice(_CHROME_UAS)
    sec_ch_ua = '"Chromium";v="128", "Not;A=Brand";v="24", "Google Chrome";v="128"'
    for key, val in _SEC_CH_UA_MAP.items():
        if key in ua:
            sec_ch_ua = val
            break
    platform = '"macOS"' if "Macintosh" in ua else ('"Linux"' if "Linux" in ua else '"Windows"')
    return {
        "User-Agent":         ua,
        "Accept-Language":    "en-US,en;q=0.9",
        "Accept-Encoding":    "gzip, deflate, br, zstd",
        "Accept":             "*/*",
        "sec-ch-ua":          sec_ch_ua,
        "sec-ch-ua-mobile":   "?0",
        "sec-ch-ua-platform": platform,
        "Sec-Fetch-Dest":     "empty",
        "Sec-Fetch-Mode":     "cors",
        "Sec-Fetch-Site":     "same-origin",
        "DNT":                "1",
        "Connection":         "keep-alive",
    }


async def human_delay(min_s: float = 2 * 60, max_s: float = 10 * 60) -> None:
    """Random delay between min_s and max_s. Default: 2-10 minutes."""
    wait = random.uniform(min_s, max_s)
    if random.random() < 0.15:
        wait += random.uniform(2 * 60, 8 * 60)
        logger.debug(f"Human delay (long break): {wait / 60:.1f}min")
    else:
        logger.debug(f"Human delay: {wait / 60:.1f}min")
    wait = max(90, wait)  # hard floor 90 seconds
    await asyncio.sleep(wait)


async def read_delay(text: str) -> None:
    words = len(text.split())
    wpm = random.randint(180, 280)
    await asyncio.sleep(max(1.5, (words / wpm) * 60))


async def compose_delay(reply_text: str) -> None:
    cps = random.uniform(3, 8)
    await asyncio.sleep(max(2.0, len(reply_text) / cps))


class RateLimiter:
    def __init__(self):
        self._history: dict[int, list[float]] = {}

    def record(self, account_id: int) -> None:
        now = time.monotonic()
        self._history.setdefault(account_id, []).append(now)
        self._history[account_id] = [t for t in self._history[account_id] if t > now - 86400]

    def count_last_30min(self, account_id: int) -> int:
        cutoff = time.monotonic() - 1800
        return sum(1 for t in self._history.get(account_id, []) if t > cutoff)

    def count_last_hour(self, account_id: int) -> int:
        cutoff = time.monotonic() - 3600
        return sum(1 for t in self._history.get(account_id, []) if t > cutoff)

    def count_today(self, account_id: int) -> int:
        cutoff = time.monotonic() - 86400
        return sum(1 for t in self._history.get(account_id, []) if t > cutoff)

    @staticmethod
    def _is_active_hours(start: int = 8, end: int = 23) -> bool:
        hour = time.localtime().tm_hour
        return start <= hour < end

    async def wait_if_needed(
        self,
        account_id: int,
        daily_limit: int = 300,
        active_hours_start: int = 8,
        active_hours_end: int = 23,
        outside_sleep_min: int = 300,
        wake_event: "asyncio.Event | None" = None,
    ) -> bool:
        if self.count_today(account_id) >= daily_limit:
            logger.warning(f"[Acc {account_id}] Daily limit reached ({daily_limit})")
            return False

        if not self._is_active_hours(active_hours_start, active_hours_end):
            wait_s = outside_sleep_min * 60
            h, m = divmod(outside_sleep_min, 60)
            label = f"{h}ч {m}м" if h else f"{m}м"
            logger.info(
                f"[Acc {account_id}] Outside active hours "
                f"({active_hours_start}:00-{active_hours_end}:00) - sleeping {label}"
            )
            if wake_event is not None:
                wake_event.clear()
                try:
                    await asyncio.wait_for(wake_event.wait(), timeout=wait_s)
                    logger.info(f"[Acc {account_id}] Outside-hours sleep interrupted by force_wake")
                except asyncio.TimeoutError:
                    pass
            else:
                await asyncio.sleep(wait_s)

        per_hour = self.count_last_hour(account_id)
        if per_hour >= 5:
            wait = random.uniform(12 * 60, 20 * 60)
            logger.info(f"[Acc {account_id}] Hourly cap ({per_hour}/5) - wait {wait / 60:.0f}min")
            await asyncio.sleep(wait)

        per_30 = self.count_last_30min(account_id)
        if per_30 >= 3:
            wait = random.uniform(8 * 60, 15 * 60)
            logger.info(f"[Acc {account_id}] Burst cap ({per_30}/3 in 30min) - wait {wait / 60:.0f}min")
            await asyncio.sleep(wait)

        return True


rate_limiter = RateLimiter()