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
        "DEBUG":    "· DEBUG  ",
        "INFO":     "ℹ INFO   ",
        "SUCCESS":  "✓ OK     ",
        "WARNING":  "⚠ WARN   ",
        "ERROR":    "✖ ERROR  ",
        "CRITICAL": "✖ CRIT   ",
    }

    def _console_fmt(record) -> str:
        lvl = record["level"].name
        icon = _LEVEL_ICONS.get(lvl, f" {lvl:<8}")
        mod = record["name"].split(".")[-1][:10]
        ts = record["time"].strftime("%H:%M:%S")
        _colors = {
            "TRACE":    ("\033[90m", "\033[0m"),
            "DEBUG":    ("\033[90m", "\033[0m"),
            "INFO":     ("\033[0m",  "\033[0m"),
            "SUCCESS":  ("\033[92m", "\033[0m"),
            "WARNING":  ("\033[93m", "\033[0m"),
            "ERROR":    ("\033[91m", "\033[0m"),
            "CRITICAL": ("\033[91m", "\033[0m"),
        }
        c_on, c_off = _colors.get(lvl, ("", ""))
        return (
            f"\033[36m{ts}\033[0m"
            f" \033[90m│\033[0m "
            f"{c_on}{icon}{c_off}"
            f" \033[90m│\033[0m "
            f"\033[35m{mod:<10}\033[0m"
            f" \033[90m│\033[0m "
            f"{c_on}{record['message']}{c_off}"
            "\n"
        )

    _file_fmt = (
        "{time:YYYY-MM-DD HH:mm:ss} | "
        "{level: <8} | "
        "{name}:{function}:{line} | "
        "{message}"
    )

    if sys.stdout is not None:
        try:
            logger.add(
                sys.stdout,
                format=_console_fmt,
                level=log_level,
                colorize=False,
                filter=_noise_filter,
            )
        except Exception:
            pass

    try:
        logger.add(
            log_file,
            format=_file_fmt,
            level=log_level,
            rotation="10 MB",
            retention="7 days",
            compression="gz",
            filter=_noise_filter,
            colorize=False,
        )
    except Exception:
        pass


_setup_logger()


# ---------------------------------------------------------------------------
# CRYPTO
# ---------------------------------------------------------------------------

_fernet: Optional[Fernet] = None


def _get_fernet() -> Fernet:
