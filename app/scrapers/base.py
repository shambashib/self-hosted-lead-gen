"""
Base scraper — shared HTTP client with retry, proxy rotation, and UA rotation.
"""
from __future__ import annotations

import asyncio
from typing import Optional
from urllib.parse import urlparse

import httpx
import structlog

from app.config import settings
from app.proxy.manager import proxy_manager, rate_limiter, ua_rotator

log = structlog.get_logger(__name__)

_RETRYABLE = (httpx.TimeoutException, httpx.ConnectError, httpx.RemoteProtocolError)


def _build_headers(ua: Optional[str] = None) -> dict:
    return {
        "User-Agent": ua or ua_rotator.next(),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9,hi;q=0.8",
        "Accept-Encoding": "gzip, deflate, br",
        "DNT": "1",
        "Connection": "keep-alive",
        "Upgrade-Insecure-Requests": "1",
    }


def _client_kwargs() -> dict:
    """Build httpx.AsyncClient kwargs — proxy uses the singular string form (httpx 0.28+)."""
    kwargs: dict = {
        "timeout": settings.request_timeout,
        "follow_redirects": True,
        "verify": False,
    }
    # httpx 0.28 removed the `proxies` dict; use `proxy` (singular string) instead.
    proxy_url = proxy_manager.next() if proxy_manager.enabled else None
    if proxy_url:
        kwargs["proxy"] = proxy_url
    return kwargs


class BaseScraper:
    def __init__(self) -> None:
        self._sem = asyncio.Semaphore(settings.max_concurrent_crawls)

    def _domain(self, url: str) -> str:
        return urlparse(url).netloc or url

    async def fetch(self, url: str, *, params: Optional[dict] = None,
                    follow_redirects: bool = True) -> Optional[str]:
        domain = self._domain(url)
        await rate_limiter.acquire(domain)
        headers = _build_headers()

        async with self._sem:
            for attempt in range(settings.max_retries):
                try:
                    async with httpx.AsyncClient(**_client_kwargs()) as client:
                        resp = await client.get(url, params=params, headers=headers)
                        resp.raise_for_status()
                        return resp.text
                except httpx.HTTPStatusError as exc:
                    if exc.response.status_code in (403, 429):
                        wait = (attempt + 1) * settings.retry_delay * 2
                        log.warning("rate_limited", url=url, status=exc.response.status_code, wait=wait)
                        await asyncio.sleep(wait)
                        headers = _build_headers()       # rotate UA
                    else:
                        log.warning("http_error", url=url, status=exc.response.status_code)
                        return None
                except _RETRYABLE as exc:
                    wait = (attempt + 1) * settings.retry_delay
                    log.warning("fetch_retry", url=url, attempt=attempt + 1, error=str(exc), wait=wait)
                    await asyncio.sleep(wait)
                except Exception as exc:
                    log.error("fetch_error", url=url, error=str(exc))
                    return None
        return None
