"""
Proxy & User-Agent rotation layer.

• ProxyManager picks the next proxy (round-robin or random).
• UARotator returns a realistic desktop/mobile user-agent string.
• RateLimiter enforces per-domain request rate.
"""
from __future__ import annotations

import asyncio
import random
import time
from collections import defaultdict
from itertools import cycle
from typing import Dict, List, Optional

import structlog

from app.config import ProxyRotation, settings

log = structlog.get_logger(__name__)

# ─── User-Agent pool ──────────────────────────────────────────────────────────
_DESKTOP_UAS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.0 Safari/605.1.15",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:132.0) Gecko/20100101 Firefox/132.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 14.7; rv:132.0) Gecko/20100101 Firefox/132.0",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36 Edg/131.0.0.0",
]


class UARotator:
    def __init__(self) -> None:
        self._pool = _DESKTOP_UAS.copy()
        self._cycle = cycle(self._pool)

    def next(self) -> str:
        return random.choice(self._pool)


# ─── Proxy manager ────────────────────────────────────────────────────────────

class ProxyManager:
    def __init__(self, proxies: List[str], rotation: ProxyRotation) -> None:
        self._proxies = proxies
        self._rotation = rotation
        self._cycle = cycle(proxies) if proxies else iter([])
        self._failures: Dict[str, int] = defaultdict(int)

    @property
    def enabled(self) -> bool:
        return bool(self._proxies) and settings.proxy_enabled

    def next(self) -> Optional[str]:
        if not self.enabled:
            return None
        if self._rotation == ProxyRotation.random:
            available = [p for p in self._proxies if self._failures[p] < 3]
            return random.choice(available) if available else None
        try:
            return next(self._cycle)
        except StopIteration:
            return None

    def report_failure(self, proxy: str) -> None:
        self._failures[proxy] += 1
        log.warning("proxy_failure", proxy=proxy, failures=self._failures[proxy])


# ─── Rate limiter ─────────────────────────────────────────────────────────────

class DomainRateLimiter:
    """Token-bucket rate limiter per domain."""

    def __init__(self, rps: float = 2.0) -> None:
        self._rps = rps
        self._last_request: Dict[str, float] = defaultdict(float)
        self._locks: Dict[str, asyncio.Lock] = defaultdict(asyncio.Lock)

    async def acquire(self, domain: str) -> None:
        async with self._locks[domain]:
            now = time.monotonic()
            gap = 1.0 / self._rps
            wait = self._last_request[domain] + gap - now
            if wait > 0:
                await asyncio.sleep(wait)
            self._last_request[domain] = time.monotonic()


# ─── Singletons ───────────────────────────────────────────────────────────────

proxy_manager = ProxyManager(settings.proxy_list_parsed, settings.proxy_rotation)
ua_rotator = UARotator()
rate_limiter = DomainRateLimiter(rps=settings.rate_limit_rps)
