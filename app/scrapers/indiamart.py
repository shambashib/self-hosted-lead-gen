"""
IndiaMART Directory Crawler.

Parses individual product/company listing pages and category search pages.
Only public, unauthenticated pages are accessed.
"""
from __future__ import annotations

import re
from typing import List, Optional
from urllib.parse import urljoin, urlparse, quote_plus

import structlog
from bs4 import BeautifulSoup

from app.models.lead import Lead, SourceType
from app.scrapers.base import BaseScraper

log = structlog.get_logger(__name__)

INDIAMART_SEARCH = "https://www.indiamart.com/search.mp?ss={query}"
INDIAMART_CAT    = "https://dir.indiamart.com/search.mp?ss={query}&src=modx"


class IndiaMARTCrawler(BaseScraper):

    async def search(self, query: str, job_id: str = "") -> List[Lead]:
        url = INDIAMART_SEARCH.format(query=quote_plus(query))
        log.info("indiamart_search", query=query, url=url)
        html = await self.fetch(url)
        if not html:
            return []
        leads = self._parse_search_results(html, query, job_id)
        log.info("indiamart_results", query=query, count=len(leads))
        return leads

    async def crawl_listing(self, url: str, job_id: str = "") -> Optional[Lead]:
        html = await self.fetch(url)
        if not html:
            return None
        return self._parse_listing(html, url, job_id)

    # ── Parsers ───────────────────────────────────────────────────────────────

    def _parse_search_results(self, html: str, query: str, job_id: str) -> List[Lead]:
        soup = BeautifulSoup(html, "lxml")
        leads: List[Lead] = []

        # IndiaMART search result cards vary by layout; try multiple selectors
        cards = (
            soup.select("div.dynamic-listing div.brs")
            or soup.select("div.listing-block")
            or soup.select("div[class*='prd-']")
        )

        for card in cards:
            lead = self._extract_card(card, query, job_id)
            if lead:
                leads.append(lead)

        return leads

    def _extract_card(self, card, query: str, job_id: str) -> Optional[Lead]:
        try:
            # Company name
            name_el = (
                card.select_one("a.p-company-name")
                or card.select_one("span.co-name")
                or card.select_one(".company-name")
                or card.select_one("h2 a")
            )
            business_name = name_el.get_text(strip=True) if name_el else None

            # Product / listing title
            title_el = card.select_one("a.prd-name") or card.select_one("h2") or card.select_one("h3")
            title = title_el.get_text(strip=True) if title_el else None

            # Phone — IndiaMART masks numbers; extract what's visible
            phone_el = (
                card.select_one("span[class*='mob']")
                or card.select_one(".mobno")
                or card.select_one("[class*='phone']")
            )
            phone_text = phone_el.get_text(strip=True) if phone_el else ""
            phone = self._clean_phone(phone_text)

            # Location
            loc_el = (
                card.select_one("span.city")
                or card.select_one(".locname")
                or card.select_one("[class*='location']")
            )
            city = loc_el.get_text(strip=True) if loc_el else None

            # URL to full listing
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
                city=city,
                industry=query,
                source_type=SourceType.indiamart,
                source_url=source_url,
                tags=["indiamart"],
            )
        except Exception as exc:
            log.debug("indiamart_card_error", error=str(exc))
            return None

    def _parse_listing(self, html: str, url: str, job_id: str) -> Optional[Lead]:
        soup = BeautifulSoup(html, "lxml")

        business_name = None
        name_el = (
            soup.select_one("span.co-name")
            or soup.select_one("h1.comp-name")
            or soup.select_one(".company-name")
        )
        if name_el:
            business_name = name_el.get_text(strip=True)

        # Phone
        phones = re.findall(r"[\+\(]?[1-9][0-9 \-\(\)]{8,}[0-9]", soup.get_text())
        phone = phones[0] if phones else None

        # Email
        emails = re.findall(r"[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}", soup.get_text())
        email = emails[0] if emails else None

        # City
        city_el = soup.select_one(".city") or soup.select_one("[class*='location']")
        city = city_el.get_text(strip=True) if city_el else None

        # Company website
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
            city=city,
            website=website,
            source_type=SourceType.indiamart,
            source_url=url,
            tags=["indiamart"],
        )

    @staticmethod
    def _clean_phone(text: str) -> Optional[str]:
        digits = re.sub(r"[^\d+]", "", text)
        if len(digits) >= 8:
            return digits
        return None
