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
            new_lines.append(line)

    if not updated:
        new_lines.append(f"{key}={value}")

    _ENV_FILE.write_text("\n".join(new_lines) + "\n", encoding="utf-8")


# ---------------------------------------------------------------------------
# SETTINGS
# ---------------------------------------------------------------------------

class AppSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=_ENV_FILE,
        env_file_encoding="utf-8",
        extra="ignore",
    )

    telegram_bot_token: str = Field(default="", description="Telegram bot token")
    telegram_admin_ids: list[int] = Field(default_factory=list)
    openai_api_key: Optional[str] = None
    gemini_api_key: Optional[str] = None
    perplexity_api_key: Optional[str] = None
    groq_api_key: Optional[str] = None
    default_ai_provider: Literal["openai", "gemini", "perplexity", "groq"] = "groq"

    db_path: Path = Field(default_factory=lambda: _DB_DIR / "xbot.db")
    log_level: str = "INFO"
    log_file: Path = Field(default_factory=lambda: _LOG_DIR / "xbot.log")
    encryption_key: str = Field(default="", description="Fernet key for encrypting cookies")

    @field_validator("telegram_admin_ids", mode="before")
    @classmethod
    def parse_admin_ids(cls, v):
        if isinstance(v, int):
            return [v]
        if isinstance(v, str):
            v = v.strip().strip("[]")
            try:
                return [int(x.strip()) for x in v.split(",") if x.strip()]
            except ValueError:
                return []
        return v or []

    def model_post_init(self, __context) -> None:
        if not self.encryption_key:
            key = Fernet.generate_key().decode()
            save_env_value("ENCRYPTION_KEY", key)
            object.__setattr__(self, "encryption_key", key)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.log_file.parent.mkdir(parents=True, exist_ok=True)


class BotDefaults:
    search_mode: Literal["keywords", "list", "recommendations"] = "keywords"
    min_likes: int = 200
    min_retweets: int = 0
    max_post_age_minutes: int = 60
    comment_sort: Literal["likes", "views"] = "likes"
    reply_mode: str = "hybrid"
    auto_publish: bool = False
    lang_filter: str = "en"
    daily_comment_limit: int = 300
    min_delay_seconds: int = 2 * 60   # 2 minutes minimum
    max_delay_seconds: int = 10 * 60  # 10 minutes maximum
    active_hours_start: int = 8       # час начала активности (0-23)
    active_hours_end: int = 23        # час конца активности (0-23)
    outside_sleep_min: int = 300      # сон вне активных часов, минут (1-1440)

    system_prompt: str = (
        "You are Marcus, an independent trader based in NYC. "
        "10+ years trading ES futures, 0DTE SPX options and BTC. "
        "You swing trade macro setups, fade retail crowding, and follow options flow closely. "
        "Skeptical of the Fed, think most retail traders overtrade. "
        "You read Zerohedge, follow @spotgamma, @SqueezeMetrics, @MacroAlf, @GameofTrades_. "
        "Outside markets: into combat sports, stoic philosophy, occasional whiskey takes.\n\n"
        "Your voice on X: dry, confident, occasionally sarcastic. "
        "You drop specific levels, flow data, or a contrarian angle -- then shut up. "
