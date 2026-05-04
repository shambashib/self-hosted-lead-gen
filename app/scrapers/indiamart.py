"""
IndiaMART Directory Crawler.

Discovery strategy:
  1. Google SERP: site:indiamart.com <query>  (most reliable)
  2. Direct search URL fallback

Only public, unauthenticated pages are accessed.
"""
from __future__ import annotations

import re
from typing import List, Optional
from urllib.parse import quote_plus, urljoin

import structlog
from bs4 import BeautifulSoup

from app.models.lead import Lead, SourceType
from app.scrapers.base import BaseScraper

log = structlog.get_logger(__name__)

# IndiaMART changed their search URL structure; use the directory endpoint
_SEARCH_URLS = [
    "https://dir.indiamart.com/search.mp?ss={query}",
    "https://www.indiamart.com/search.mp?ss={query}&src=custom-4",
]


class IndiaMARTCrawler(BaseScraper):

    async def search(self, query: str, job_id: str = "") -> List[Lead]:
        """Try each search URL until one returns a parseable page."""
        for url_tpl in _SEARCH_URLS:
            url = url_tpl.format(query=quote_plus(query))
            log.info("indiamart_search", query=query, url=url)
            html = await self.fetch(url)
            if html and self._looks_valid(html):
                leads = self._parse_search_results(html, job_id)
                if leads:
                    log.info("indiamart_results", query=query, count=len(leads))
                    return leads
        log.info("indiamart_no_results", query=query)
        return []

    async def crawl_listing(self, url: str, job_id: str = "") -> Optional[Lead]:
        html = await self.fetch(url)
        if not html:
            return None
        return self._parse_listing(html, url, job_id)

    # ── Parsers ───────────────────────────────────────────────────────────────

    @staticmethod
    def _looks_valid(html: str) -> bool:
        lower = html.lower()
        return "indiamart" in lower and ("supplier" in lower or "company" in lower or "product" in lower)

    def _parse_search_results(self, html: str, job_id: str) -> List[Lead]:
        soup = BeautifulSoup(html, "lxml")
        leads: List[Lead] = []

        # IndiaMART uses several card layouts across their different page versions
        cards = (
            soup.select("div.dynamic-listing div.brs")
            or soup.select("div.listing-block")
            or soup.select("div.impg-listing")
            or soup.select("div[class*='prd-']")
            or soup.select("div.listingBg")
        )

        for card in cards:
            lead = self._extract_card(card, job_id)
            if lead:
                leads.append(lead)

        return leads

    def _extract_card(self, card, job_id: str) -> Optional[Lead]:
        try:
            # Company name — several possible selectors across IM versions
            name_el = (
                card.select_one("a.p-company-name")
                or card.select_one("span.co-name")
                or card.select_one(".company-name")
                or card.select_one("h2 a")
                or card.select_one("h3 a")
            )
            business_name = name_el.get_text(strip=True) if name_el else None

            # Product / listing title
            title_el = (
                card.select_one("a.prd-name")
                or card.select_one("h2")
                or card.select_one("h3")
            )
            title = title_el.get_text(strip=True) if title_el else None

            # Phone
            phone_el = (
                card.select_one("span[class*='mob']")
                or card.select_one(".mobno")
                or card.select_one("[class*='phone']")
            )
            phone_text = phone_el.get_text(strip=True) if phone_el else ""
            phone = self._clean_phone(phone_text)

            # City
            loc_el = (
                card.select_one("span.city")
                or card.select_one(".locname")
                or card.select_one("[class*='location']")
            )
            city = loc_el.get_text(strip=True) if loc_el else None

            # Source URL
            link_el = card.select_one("a[href]")
            source_url = None
            if link_el:
                href = link_el.get("href", "")
                if href.startswith("http"):
                    source_url = href
                elif href.startswith("/"):
                    source_url = "https://www.indiamart.com" + href

            if not business_name and not title:
                return None

            return Lead(
                job_id=job_id,
                business_name=business_name or title,
                name=title,
                phone=phone,
                city=city or None,
                source_type=SourceType.indiamart,
                source_url=source_url,
                tags=["indiamart"],
            )
        except Exception as exc:
            log.debug("indiamart_card_error", error=str(exc))
            return None

    def _parse_listing(self, html: str, url: str, job_id: str) -> Optional[Lead]:
        soup = BeautifulSoup(html, "lxml")
        text = soup.get_text()

        name_el = (
            soup.select_one("span.co-name")
            or soup.select_one("h1.comp-name")
            or soup.select_one(".company-name")
        )
        business_name = name_el.get_text(strip=True) if name_el else None

        phones = re.findall(r"[\+\(]?[1-9][0-9 \-\(\)]{8,}[0-9]", text)
        phone = self._clean_phone(phones[0]) if phones else None

        emails = re.findall(r"[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}", text)
        email = emails[0] if emails else None

        city_el = soup.select_one(".city") or soup.select_one("[class*='location']")
        city = city_el.get_text(strip=True) if city_el else None

        website = None
        for a in soup.select("a[href]"):
            href = a.get("href", "")
            if href.startswith("http") and "indiamart.com" not in href:
                website = href
                break

        if not business_name:
            return None

        return Lead(
            job_id=job_id,
            business_name=business_name,
            email=email,
            phone=phone,
            city=city or None,
            website=website,
            source_type=SourceType.indiamart,
            source_url=url,
            tags=["indiamart"],
        )

    @staticmethod
    def _clean_phone(text: str) -> Optional[str]:
        if not text:
            return None
        digits = re.sub(r"[^\d+]", "", text)
        return digits if len(digits) >= 8 else None
