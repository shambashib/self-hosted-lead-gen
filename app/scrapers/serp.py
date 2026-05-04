"""
SERP Scraper — uses Brave Search API when BRAVE_SEARCH_API_KEY is set,
falls back to Google HTML scraping otherwise.

Brave API docs: https://api.search.brave.com/res/v1/web/search
  Headers: X-Subscription-Token, Accept: application/json
  Params:  q, count (max 20), country=in, search_lang=en

Google HTML fallback (multi-tier):
  Tier 1 — anchor-on-h3 (most stable signal)
  Tier 2 — /url?q= redirect decoding
  Tier 3 — all outgoing https:// links
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import List, Optional
from urllib.parse import parse_qs, quote_plus, unquote, urlparse

import httpx
import structlog

from app.config import settings
from app.scrapers.base import BaseScraper

log = structlog.get_logger(__name__)

# ── Brave Search API ──────────────────────────────────────────────────────────

BRAVE_SEARCH_URL = "https://api.search.brave.com/res/v1/web/search"

# ── Google HTML fallback ──────────────────────────────────────────────────────

GOOGLE_SEARCH_URL = "https://www.google.com/search"

_GOOGLE_DOMAINS = re.compile(
    r"(google\.|googleadservices\.|googleapis\.|gstatic\.|"
    r"youtube\.|facebook\.|twitter\.|instagram\.|linkedin\.com/in/)"
)
_SKIP_PATHS = {"/search", "/maps", "/images", "/shopping", "/news", "/videos"}


@dataclass
class SERPResult:
    url: str
    title: str = ""
    snippet: str = ""


# ── Brave API client ──────────────────────────────────────────────────────────

class BraveSearchScraper:
    """Calls the Brave Search API — no scraping, clean JSON responses."""

    async def search(self, query: str, num: int = 10) -> List[SERPResult]:
        count = min(num, 20)  # Brave allows up to 20 per request
        params = {
            "q": query,
            "count": count,
            "country": "in",
            "search_lang": "en",
            "result_filter": "web",
        }
        headers = {
            "Accept": "application/json",
            "Accept-Encoding": "gzip",
            "X-Subscription-Token": settings.brave_search_api_key,
        }

        log.info("brave_search", query=query)
        try:
            async with httpx.AsyncClient(timeout=settings.request_timeout) as client:
                resp = await client.get(BRAVE_SEARCH_URL, params=params, headers=headers)
                resp.raise_for_status()
                data = resp.json()
        except httpx.HTTPStatusError as exc:
            log.warning("brave_search_http_error", status=exc.response.status_code, query=query)
            return []
        except Exception as exc:
            log.warning("brave_search_failed", query=query, error=str(exc))
            return []

        results: List[SERPResult] = []
        for item in data.get("web", {}).get("results", []):
            url = item.get("url", "")
            if not url:
                continue
            results.append(SERPResult(
                url=url,
                title=item.get("title", ""),
                snippet=item.get("description", ""),
            ))

        log.info("brave_search_parsed", count=len(results))
        return results


# ── Google HTML fallback ──────────────────────────────────────────────────────

def _decode_href(href: str) -> Optional[str]:
    """Unwrap /url?q=... Google redirect links and filter internal URLs."""
    if href.startswith("/url?"):
        qs = parse_qs(urlparse(href).query)
        href = qs.get("q", [href])[0]
    href = unquote(href)
    if not href.startswith("http"):
        return None
    parsed = urlparse(href)
    if _GOOGLE_DOMAINS.search(parsed.netloc):
        return None
    if parsed.path in _SKIP_PATHS:
        return None
    return href


class GoogleSERPScraper(BaseScraper):

    async def search(self, query: str, num: int = 10) -> List[SERPResult]:
        url = (
            f"{GOOGLE_SEARCH_URL}"
            f"?q={quote_plus(query)}&num={min(num, 10)}&hl=en&gl=in"
        )
        log.info("google_serp_search", query=query)

        html = await self.fetch(url)
        if not html:
            log.warning("google_serp_empty", query=query)
            return []

        results = self._parse(html)
        log.info("google_serp_parsed", count=len(results))
        return results

    def _parse(self, html: str) -> List[SERPResult]:
        from bs4 import BeautifulSoup
        soup = BeautifulSoup(html, "lxml")
        seen: set = set()
        results: List[SERPResult] = []

        # Tier 1: h3-anchored results
        for h3 in soup.find_all("h3"):
            a = h3.find_parent("a") or h3.find_previous_sibling("a") or h3.find_parent().find("a")  # type: ignore[union-attr]
            if not a:
                continue
            href = _decode_href(a.get("href", ""))
            if not href or href in seen:
                continue
            seen.add(href)
            title = h3.get_text(strip=True)
            snippet = ""
            container = h3.find_parent("div")
            for _ in range(4):
                if not container:
                    break
                for span in container.find_all(["span", "div"]):
                    text = span.get_text(" ", strip=True)
                    if len(text) > 60 and text != title:
                        snippet = text[:300]
                        break
                if snippet:
                    break
                container = container.find_parent("div")
            results.append(SERPResult(url=href, title=title, snippet=snippet))

        if results:
            return results

        # Tier 2: /url?q= redirect links
        for a in soup.find_all("a", href=True):
            href = a.get("href", "")
            if not href.startswith("/url?"):
                continue
            decoded = _decode_href(href)
            if decoded and decoded not in seen:
                seen.add(decoded)
                results.append(SERPResult(url=decoded, title=a.get_text(strip=True)[:120]))

        if results:
            return results

        # Tier 3: any outgoing https:// link
        for a in soup.find_all("a", href=True):
            href = a.get("href", "")
            decoded = _decode_href(href)
            if decoded and decoded not in seen:
                seen.add(decoded)
                results.append(SERPResult(url=decoded, title=a.get_text(strip=True)[:120]))

        return results


# ── Factory — pick the right scraper based on config ─────────────────────────

def make_serp_scraper() -> BraveSearchScraper | GoogleSERPScraper:
    """Return BraveSearchScraper if an API key is configured, else Google HTML."""
    if settings.brave_search_api_key:
        log.info("serp_backend", backend="brave")
        return BraveSearchScraper()
    log.info("serp_backend", backend="google_html")
    return GoogleSERPScraper()
