"""
Generic Company Website Crawler.

Visits homepage → discovers /contact, /about → extracts contact info.
Falls back to Playwright for JS-heavy pages.
"""
from __future__ import annotations

import re
from typing import List, Optional, Set
from urllib.parse import urljoin, urlparse

import structlog
from bs4 import BeautifulSoup

from app.config import settings
from app.models.lead import Lead, SourceType
from app.scrapers.base import BaseScraper

log = structlog.get_logger(__name__)

# High-value pages to look for
_CONTACT_SLUGS = {
    "contact", "contact-us", "contactus", "get-in-touch", "reach-us",
    "about", "about-us", "aboutus", "team",
}


class WebsiteCrawler(BaseScraper):

    async def crawl(self, url: str, job_id: str = "") -> Optional[Lead]:
        """Crawl a company website and return a lead with extracted contact info."""
        if not url.startswith("http"):
            url = "https://" + url

        log.info("website_crawl", url=url)
        base_html = await self._get_page(url)
        if not base_html:
            return None

        pages_html = [base_html]
        contact_urls = self._find_contact_pages(base_html, url)

        for cu in list(contact_urls)[:3]:          # max 3 sub-pages
            ch = await self._get_page(cu)
            if ch:
                pages_html.append(ch)

        combined = "\n".join(pages_html)
        return self._build_lead(combined, url, job_id)

    # ── Internal helpers ──────────────────────────────────────────────────────

    async def _get_page(self, url: str) -> Optional[str]:
        html = await self.fetch(url)
        if html:
            return html
        # JS rendering fallback (runs sync Playwright in a thread — Windows-safe)
        from app.scrapers.playwright_helper import playwright_fetch
        return await playwright_fetch(url)

    def _find_contact_pages(self, html: str, base_url: str) -> Set[str]:
        soup = BeautifulSoup(html, "lxml")
        found: Set[str] = set()
        for a in soup.select("a[href]"):
            href = a.get("href", "").strip().lower().split("?")[0].rstrip("/")
            slug = href.rsplit("/", 1)[-1]
            if any(s in slug for s in _CONTACT_SLUGS):
                full = urljoin(base_url, a.get("href", ""))
                if urlparse(full).netloc == urlparse(base_url).netloc:
                    found.add(full)
        return found

    def _build_lead(self, html: str, source_url: str, job_id: str) -> Optional[Lead]:
        from app.extractors.contact import ContactExtractor
        from app.extractors.social import SocialExtractor

        extractor = ContactExtractor()
        social_ex = SocialExtractor()

        emails = extractor.emails(html)
        phones = extractor.phones(html)
        socials = social_ex.extract(html)

        soup = BeautifulSoup(html, "lxml")

        # Business name from title or og:site_name
        business_name = None
        og_site = soup.find("meta", property="og:site_name")
        if og_site:
            business_name = og_site.get("content", "").strip()
        if not business_name:
            title = soup.find("title")
            if title:
                business_name = title.get_text(strip=True).split("|")[0].split("-")[0].strip()

        if not emails and not phones:
            return None

        return Lead(
            job_id=job_id,
            business_name=business_name,
            email=emails[0] if emails else None,
            phone=phones[0] if phones else None,
            website=source_url,
            social_links=socials,
            source_type=SourceType.company_website,
            source_url=source_url,
            tags=["company_website"],
        )
