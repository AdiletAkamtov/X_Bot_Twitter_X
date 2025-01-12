"""
proxy.py — Proxy rotation manager (HTTP / SOCKS5 / Residential)
"""

from __future__ import annotations

import random
from typing import Optional

from config import logger


class ProxyManager:
    """Loads proxies from DB and rotates them per account."""

    def __init__(self):
        self._proxies: list[dict] = []

    async def reload(self) -> None:
        from db import get_proxies

        self._proxies = await get_proxies(active_only=True)
        logger.info(f"Loaded {len(self._proxies)} proxies")

    async def get_proxy_for_account(
        self, proxy_id: Optional[int] = None
    ) -> Optional[dict]:
        if not self._proxies:
            return None
        if proxy_id:
            for p in self._proxies:
                if p["id"] == proxy_id:
                    return p
        return random.choice(self._proxies)

    @staticmethod
    def build_httpx_proxy(proxy: dict) -> str:
        """Convert proxy dict to httpx-compatible proxy URL string."""
        return proxy["url"]

    async def mark_failed(self, proxy_id: int) -> None:
        from db import mark_proxy_failed

        await mark_proxy_failed(proxy_id)
        # Increment in-memory fail_count so the eviction check uses the current value
        for p in self._proxies:
            if p["id"] == proxy_id:
                p["fail_count"] = p.get("fail_count", 0) + 1
        # Evict proxy from session once it has hit the failure threshold
        self._proxies = [
            p
            for p in self._proxies
            if not (p["id"] == proxy_id and p.get("fail_count", 0) >= 3)
        ]

    async def mark_success(self, proxy_id: int) -> None:
        from db import reset_proxy_fails

        await reset_proxy_fails(proxy_id)


proxy_manager = ProxyManager()
