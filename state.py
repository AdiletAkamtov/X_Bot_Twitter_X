"""
state.py — Shared mutable runtime state.

Extracted to break the circular dependency between main.py and tg_bot.py:
  main.py   imports tg_bot.py  (top-level, for build_application / register_handlers)
  tg_bot.py imports main.py    (was deferred, for _pending_queue / worker_manager)

Both modules now import from this neutral module instead.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Optional

if TYPE_CHECKING:
    from main import WorkerManager

# ── Pending HITL queue ────────────────────────────────────────────────────────
# Keyed by account_id → list of pending approval items.
pending_queue: dict[int, list[dict]] = {}

# Callbacks registered by main.py to be called when a new item enters the queue.
pending_callbacks: list = []

# ── Global Telegram Application reference ──────────────────────────────────────────
tg_app: Optional[Any] = None  # telegram.ext.Application instance

# ── Worker manager reference (set by main.py after WorkerManager is created) ──
worker_manager: Optional["WorkerManager"] = None
