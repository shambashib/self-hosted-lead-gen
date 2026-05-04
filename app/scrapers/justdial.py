"""
JustDial Directory Crawler.

JustDial uses heavy JS rendering; we first try a static fetch (fast),
then fall back to Playwright if USE_PLAYWRIGHT=true.

Public pages only — no login, no API bypass.
"""
from __future__ import annotations

import re
from typing import List, Optional
from urllib.parse import quote_plus, urlparse

import structlog
from bs4 import BeautifulSoup

from app.config import settings
from app.models.lead import Lead, SourceType
from app.scrapers.base import BaseScraper

log = structlog.get_logger(__name__)

JUSTDIAL_SEARCH = "https://www.justdial.com/{city}/{query}"

# JustDial renders "Show Number" / "Get Phone Number" as placeholder text until
# the user clicks; these strings are NOT real phone numbers.
_PHONE_JUNK = {"show number", "get phone number", "call now", "click to call", ""}


class JustDialCrawler(BaseScraper):

    async def search(self, query: str, city: str = "India", job_id: str = "") -> List[Lead]:
        city_slug = city.replace(" ", "-").lower()
        query_slug = query.replace(" ", "-")
        url = JUSTDIAL_SEARCH.format(city=city_slug.title(), query=query_slug)
        log.info("justdial_search", query=query, city=city, url=url)

        html = await self._get_html(url)
        if not html:
            return []

        leads = self._parse(html, job_id)
        log.info("justdial_results", query=query, count=len(leads))
        return leads

    async def _get_html(self, url: str) -> Optional[str]:
        html = await self.fetch(url)
        if html and self._looks_valid(html):
            return html
        # JS rendering fallback (runs sync Playwright in a thread — Windows-safe)
        from app.scrapers.playwright_helper import playwright_fetch
        return await playwright_fetch(url)

    @staticmethod
    def _looks_valid(html: str) -> bool:
        return "jdmart" in html.lower() or "justdial" in html.lower() or "company" in html.lower()

    def _parse(self, html: str, job_id: str) -> List[Lead]:
        soup = BeautifulSoup(html, "lxml")
        leads: List[Lead] = []

        cards = (
            soup.select("li.cntanr")
            or soup.select("div[class*='resultbox']")
            or soup.select("div.jsx-resultbox")
            or soup.select("li[class*='list-']")
        )

        for card in cards:
            lead = self._extract_card(card, job_id)
            if lead:
                leads.append(lead)

        return leads

    def _extract_card(self, card, job_id: str) -> Optional[Lead]:
        try:
            # Business name
            name_el = (
                card.select_one("span.lng_cont_name")
                or card.select_one("a.component_name")
                or card.select_one("h2 a")
                or card.select_one("[class*='comp-name']")
            )
            business_name = name_el.get_text(strip=True) if name_el else None
            if not business_name:
                return None

            # Phone — JustDial obfuscates; try data-* first, then regex on raw text.
            # Reject placeholder strings like "Show Number".
            phone = None
            phone_el = card.select_one("[class*='callcontent']") or card.select_one("[data-phone]")
            if phone_el:
                raw = phone_el.get("data-phone") or phone_el.get_text(strip=True)
                if raw and raw.lower().strip() not in _PHONE_JUNK:
                    phone = raw
            if not phone:
                digits = re.findall(r"[789]\d{9}", card.get_text())
                phone = digits[0] if digits else None

            # Address
            addr_el = (
                card.select_one("span.cont_fl_addr")
                or card.select_one("[class*='address']")
            )
            raw_addr = addr_el.get_text(strip=True) if addr_el else None
            address = raw_addr if raw_addr else None  # never store empty string

            # Rating
            rating_el = card.select_one("span.green-box") or card.select_one("[class*='rating']")
            tags = ["justdial"]
            if rating_el:
                r = rating_el.get_text(strip=True)
                if r:
                    tags.append(f"rating:{r}")

            # Source URL + city extracted from URL path
            a_el = card.select_one("a[href]")
            source_url = None
            city = None
            if a_el:
                href = a_el.get("href", "")
                if href.startswith("http"):
                    source_url = href
                elif href.startswith("/"):
                    source_url = "https://www.justdial.com" + href
                if source_url:
                    city = self._city_from_url(source_url)

            return Lead(
                job_id=job_id,
                business_name=business_name,
                phone=phone,
                address=address,
                city=city,
                source_type=SourceType.justdial,
                source_url=source_url,
                tags=tags,
            )
        except Exception as exc:
            log.debug("justdial_card_error", error=str(exc))
            return None

    @staticmethod
    def _city_from_url(url: str) -> Optional[str]:
        """Extract city name from JustDial URL path: /Mumbai/Business-Name/..."""
        try:
            path = urlparse(url).path.lstrip("/")
            segment = path.split("/")[0]
            # JustDial city slugs are capitalized city names
            if segment and segment[0].isupper() and len(segment) > 2:
                return segment.replace("-", " ")
        except Exception:
            pass
        return None
