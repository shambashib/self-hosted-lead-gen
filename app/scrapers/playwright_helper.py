"""
Playwright helper — runs Chromium in a ThreadPoolExecutor using the sync API.

On Windows, asyncio's ProactorEventLoop inside uvicorn's reloader context
cannot spawn subprocesses via create_subprocess_exec (raises NotImplementedError).
The fix is to run sync Playwright inside a thread, then await the thread future.
"""
from __future__ import annotations

import asyncio
import concurrent.futures
from typing import Optional

import structlog

from app.config import settings
from app.proxy.manager import ua_rotator

log = structlog.get_logger(__name__)

_executor = concurrent.futures.ThreadPoolExecutor(max_workers=3, thread_name_prefix="pw")


def _sync_fetch(url: str, ua: str) -> Optional[str]:
    """Blocking Playwright fetch — runs in a thread."""
    try:
        from playwright.sync_api import sync_playwright
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            ctx = browser.new_context(user_agent=ua)
            page = ctx.new_page()
            page.goto(url, timeout=settings.request_timeout * 1000, wait_until="domcontentloaded")
            html = page.content()
            browser.close()
            return html
    except Exception as exc:
        log.warning("playwright_sync_failed", url=url, error=str(exc))
        return None


async def playwright_fetch(url: str) -> Optional[str]:
    """Async wrapper — offloads sync Playwright to a thread executor."""
    if not settings.use_playwright:
        return None
    loop = asyncio.get_event_loop()
    ua = ua_rotator.next()
    try:
        return await loop.run_in_executor(_executor, _sync_fetch, url, ua)
    except Exception as exc:
        log.warning("playwright_executor_failed", url=url, error=str(exc))
        return None
