"""
Google SERP Scraper.

Uses the public HTML search page (no API key required).
Extracts: URL, title, snippet for each organic result.

Anti-detection measures:
  • Random user-agent per request
  • Per-domain rate limiting
  • Exponential backoff on 429/403
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import List, Optional
from urllib.parse import quote_plus, urljoin

import structlog
from bs4 import BeautifulSoup

from app.config import settings
from app.scrapers.base import BaseScraper

log = structlog.get_logger(__name__)

GOOGLE_SEARCH_URL = "https://www.google.com/search"


@dataclass
class SERPResult:
    url: str
    title: str = ""
    snippet: str = ""


class GoogleSERPScraper(BaseScraper):
    async def search(self, query: str, num: int = 10) -> List[SERPResult]:
        params = {
            "q": query,
            "num": min(num, 10),
            "hl": "en",
            "gl": "in",
        }
        url = f"{GOOGLE_SEARCH_URL}?q={quote_plus(query)}&num={params['num']}&hl=en&gl=in"
        log.info("serp_search", query=query)

        html = await self.fetch(url)
        if not html:
            log.warning("serp_empty", query=query)
            return []

        return self._parse(html)

    def _parse(self, html: str) -> List[SERPResult]:
        soup = BeautifulSoup(html, "lxml")
        results: List[SERPResult] = []

        # Google wraps each result in a <div class="g"> or similar
        for div in soup.select("div.g, div[data-sokoban-feature]"):
            a_tag = div.select_one("a[href]")
            if not a_tag:
                continue

            href = a_tag.get("href", "")
            # Filter out Google internal links
            if not href.startswith("http") or "google.com" in href:
                continue

            title_el = div.select_one("h3")
            title = title_el.get_text(strip=True) if title_el else ""

            # Snippet lives in various span structures depending on Google version
            snippet_el = (
                div.select_one("div[data-sncf]")
                or div.select_one("span.aCOpRe")
                or div.select_one("div.IsZvec span")
                or div.select_one("div.VwiC3b")
            )
            snippet = snippet_el.get_text(" ", strip=True) if snippet_el else ""

            results.append(SERPResult(url=href, title=title, snippet=snippet))

        # Fallback: grab all outgoing links if structured parse fails
        if not results:
            for a in soup.select("a[href]"):
                href = a.get("href", "")
                if href.startswith("http") and "google.com" not in href:
                    results.append(SERPResult(url=href, title=a.get_text(strip=True)))

        log.info("serp_parsed", count=len(results))
        return results
