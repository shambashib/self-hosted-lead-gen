"""
JustDial Directory Crawler.

JustDial uses heavy JS rendering; we first try a static fetch (fast),
then fall back to Playwright if USE_PLAYWRIGHT=true.

Public pages only — no login, no API bypass.
"""
from __future__ import annotations

import re
from typing import List, Optional
from urllib.parse import quote_plus

import structlog
from bs4 import BeautifulSoup

from app.config import settings
from app.models.lead import Lead, SourceType
from app.scrapers.base import BaseScraper

log = structlog.get_logger(__name__)

JUSTDIAL_SEARCH = "https://www.justdial.com/{city}/{query}"
JUSTDIAL_FALLBACK = "https://www.justdial.com/jdmart/{city}/{query}/page-1"


class JustDialCrawler(BaseScraper):

    async def search(self, query: str, city: str = "India", job_id: str = "") -> List[Lead]:
        city_slug = city.replace(" ", "-").lower()
        query_slug = query.replace(" ", "-")
        url = JUSTDIAL_SEARCH.format(city=city_slug.title(), query=query_slug)
        log.info("justdial_search", query=query, city=city, url=url)

        html = await self._get_html(url)
        if not html:
            return []

        leads = self._parse(html, query, job_id)
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

    def _parse(self, html: str, query: str, job_id: str) -> List[Lead]:
        soup = BeautifulSoup(html, "lxml")
        leads: List[Lead] = []

        # JustDial listing cards
        cards = (
            soup.select("li.cntanr")
            or soup.select("div[class*='resultbox']")
            or soup.select("div.jsx-resultbox")
            or soup.select("li[class*='list-']")
        )

        for card in cards:
            lead = self._extract_card(card, query, job_id)
            if lead:
                leads.append(lead)

        return leads

    def _extract_card(self, card, query: str, job_id: str) -> Optional[Lead]:
        try:
            # Business name
            name_el = (
                card.select_one("span.lng_cont_name")
                or card.select_one("a.component_name")
                or card.select_one("h2 a")
                or card.select_one("[class*='comp-name']")
            )
            business_name = name_el.get_text(strip=True) if name_el else None

            # Phone — JustDial obfuscates; grab data-* attrs or visible text
            phone = None
            phone_el = card.select_one("[class*='callcontent']") or card.select_one("[data-phone]")
            if phone_el:
                phone = phone_el.get("data-phone") or phone_el.get_text(strip=True)
            if not phone:
                phones = re.findall(r"[789]\d{9}", card.get_text())
                phone = phones[0] if phones else None

            # Address / city
            addr_el = (
                card.select_one("span.cont_fl_addr")
                or card.select_one("[class*='address']")
            )
            address = addr_el.get_text(strip=True) if addr_el else None

            # Rating (store as tag)
            rating_el = card.select_one("span.green-box") or card.select_one("[class*='rating']")
            tags = ["justdial"]
            if rating_el:
                tags.append(f"rating:{rating_el.get_text(strip=True)}")

            # Link
            a_el = card.select_one("a[href]")
            source_url = None
            if a_el:
                href = a_el.get("href", "")
                if href.startswith("http"):
                    source_url = href
                elif href.startswith("/"):
                    source_url = "https://www.justdial.com" + href

            if not business_name:
                return None

            return Lead(
                job_id=job_id,
                business_name=business_name,
                phone=phone,
                address=address,
                industry=query,
                source_type=SourceType.justdial,
                source_url=source_url,
                tags=tags,
            )
        except Exception as exc:
            log.debug("justdial_card_error", error=str(exc))
            return None
