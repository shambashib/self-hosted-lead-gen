"""
LinkedIn Company Scraper.

Strategy (compliant, no login required):
  1. Discover company URLs via Google SERP: site:linkedin.com/company <query>
  2. Visit each public /company/<slug> page
  3. Extract: name, tagline, website, location, follower count

Anti-ban measures (mimicking Firecrawl's approach):
  • 5–10 s random delay between LinkedIn requests (well below rate limits)
  • Randomised user-agent per request
  • Playwright with stealth-mode headers (disabled automation flags)
  • Proxy rotation if configured
  • Hard cap: MAX_LINKEDIN_PER_JOB profiles (default 5) — keeps us under radar
  • LinkedIn company pages only — NO profile/people pages, NO login

Public data only — LinkedIn company pages are indexed by Google and are
accessible without authentication.
"""
from __future__ import annotations

import asyncio
import random
import re
from typing import List, Optional
from urllib.parse import quote_plus, urlparse

import structlog
from bs4 import BeautifulSoup

from app.config import settings
from app.models.lead import Lead, SocialLinks, SourceType
from app.scrapers.base import BaseScraper
from app.scrapers.serp import GoogleSERPScraper

log = structlog.get_logger(__name__)

MAX_LINKEDIN_PER_JOB = 5        # hard cap per pipeline run
_LI_DELAY_MIN = 5.0             # minimum seconds between LinkedIn requests
_LI_DELAY_MAX = 10.0            # maximum seconds between LinkedIn requests

# Stealth headers that make the request look like a normal browser navigation
_STEALTH_HEADERS = {
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Accept-Encoding": "gzip, deflate, br",
    "Cache-Control": "max-age=0",
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "none",
    "Sec-Fetch-User": "?1",
    "Upgrade-Insecure-Requests": "1",
    "DNT": "1",
}


class LinkedInScraper(BaseScraper):

    def __init__(self) -> None:
        super().__init__()
        self._serp = GoogleSERPScraper()

    async def search(self, query: str, job_id: str = "") -> List[Lead]:
        """Discover LinkedIn company URLs via Google, then scrape each page."""
        serp_query = f'site:linkedin.com/company {query}'
        log.info("linkedin_discover", query=serp_query)

        serp_results = await self._serp.search(serp_query, num=MAX_LINKEDIN_PER_JOB + 2)
        company_urls = [
            r.url for r in serp_results
            if "linkedin.com/company/" in r.url
            and "/posts" not in r.url
            and "/jobs" not in r.url
        ][:MAX_LINKEDIN_PER_JOB]

        if not company_urls:
            log.info("linkedin_no_urls", query=query)
            return []

        log.info("linkedin_urls_found", count=len(company_urls), urls=company_urls)

        leads: List[Lead] = []
        for url in company_urls:
            # Random delay between requests — critical for not getting blocked
            delay = random.uniform(_LI_DELAY_MIN, _LI_DELAY_MAX)
            log.info("linkedin_delay", seconds=round(delay, 1), url=url)
            await asyncio.sleep(delay)

            lead = await self._scrape_company(url, job_id)
            if lead:
                leads.append(lead)

        log.info("linkedin_leads", count=len(leads))
        return leads

    async def _scrape_company(self, url: str, job_id: str) -> Optional[Lead]:
        """Scrape a single public LinkedIn company page."""
        html = await self._fetch_with_stealth(url)
        if not html:
            # Try Playwright as fallback (LinkedIn is heavily JS-rendered)
            html = await self._playwright_stealth(url)
        if not html:
            return None
        return self._parse_company_page(html, url, job_id)

    async def _fetch_with_stealth(self, url: str) -> Optional[str]:
        """HTTP fetch with stealth headers and proxy if available."""
        from app.proxy.manager import ua_rotator, proxy_manager
        import httpx

        headers = {**_STEALTH_HEADERS, "User-Agent": ua_rotator.next()}
        proxy_url = proxy_manager.next() if proxy_manager.enabled else None

        try:
            client_kwargs = {
                "timeout": settings.request_timeout,
                "follow_redirects": True,
                "verify": False,
            }
            if proxy_url:
                client_kwargs["proxy"] = proxy_url

            async with httpx.AsyncClient(**client_kwargs) as client:
                resp = await client.get(url, headers=headers)
                if resp.status_code == 200:
                    return resp.text
                if resp.status_code in (429, 999):
                    log.warning("linkedin_rate_limited", url=url, status=resp.status_code)
                else:
                    log.debug("linkedin_http_error", url=url, status=resp.status_code)
        except Exception as exc:
            log.debug("linkedin_fetch_error", url=url, error=str(exc))
        return None

    async def _playwright_stealth(self, url: str) -> Optional[str]:
        """Playwright fetch with automation-detection bypass."""
        if not settings.use_playwright:
            return None

        import concurrent.futures
        loop = asyncio.get_event_loop()
        from app.proxy.manager import ua_rotator
        ua = ua_rotator.next()

        def _sync():
            try:
                from playwright.sync_api import sync_playwright
                with sync_playwright() as p:
                    browser = p.chromium.launch(
                        headless=True,
                        args=[
                            "--disable-blink-features=AutomationControlled",
                            "--no-sandbox",
                            "--disable-dev-shm-usage",
                        ],
                    )
                    ctx = browser.new_context(
                        user_agent=ua,
                        viewport={"width": 1280, "height": 800},
                        locale="en-US",
                        extra_http_headers=_STEALTH_HEADERS,
                    )
                    # Patch navigator.webdriver to false
                    ctx.add_init_script("Object.defineProperty(navigator,'webdriver',{get:()=>undefined})")
                    page = ctx.new_page()
                    page.goto(url, timeout=settings.request_timeout * 1000, wait_until="domcontentloaded")
                    html = page.content()
                    browser.close()
                    return html
            except Exception as exc:
                log.warning("linkedin_playwright_failed", url=url, error=str(exc))
                return None

        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as ex:
            return await loop.run_in_executor(ex, _sync)

    def _parse_company_page(self, html: str, source_url: str, job_id: str) -> Optional[Lead]:
        soup = BeautifulSoup(html, "lxml")
        text = soup.get_text(" ", strip=True)

        # ── Company name ──────────────────────────────────────────────────────
        name = None
        # Try structured meta first
        og_title = soup.find("meta", property="og:title")
        if og_title:
            name = og_title.get("content", "").split("|")[0].strip()
        if not name:
            h1 = soup.find("h1")
            name = h1.get_text(strip=True) if h1 else None
        if not name:
            # Extract slug from URL: linkedin.com/company/<slug>
            m = re.search(r"linkedin\.com/company/([^/?#]+)", source_url)
            if m:
                name = m.group(1).replace("-", " ").title()

        # ── Website ───────────────────────────────────────────────────────────
        website = None
        for a in soup.select("a[href]"):
            href = a.get("href", "")
            if (
                href.startswith("http")
                and "linkedin.com" not in href
                and "google.com" not in href
                and len(href) < 200
            ):
                website = href
                break

        # ── Location ──────────────────────────────────────────────────────────
        city = None
        loc_el = (
            soup.select_one("[class*='headquarters']")
            or soup.select_one("[class*='location']")
        )
        if loc_el:
            raw = loc_el.get_text(strip=True)
            # "Mumbai, Maharashtra, India" → take first segment
            city = raw.split(",")[0].strip() or None

        # ── Follower count as tag ─────────────────────────────────────────────
        tags = ["linkedin"]
        followers_m = re.search(r"([\d,]+)\s+followers?", text, re.I)
        if followers_m:
            tags.append(f"followers:{followers_m.group(1)}")

        # ── Email extraction from page ────────────────────────────────────────
        from app.extractors.contact import ContactExtractor
        emails = ContactExtractor().emails(html)

        if not name:
            return None

        return Lead(
            job_id=job_id,
            business_name=name,
            email=emails[0] if emails else None,
            website=website,
            city=city,
            social_links=SocialLinks(linkedin=source_url),
            source_type=SourceType.company_website,
            source_url=source_url,
            tags=tags,
        )
