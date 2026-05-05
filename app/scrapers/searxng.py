"""
SearXNG Search Scraper.

Direct Python port of Firecrawl's searxng_search() TypeScript function.

Key behaviours preserved:
  - resultsPerPage = 20 (SearXNG default)
  - Paginates to satisfy num_results — stops early if a page returns nothing
  - Slices final list to exactly num_results
  - Supports lang, engines, categories, pageno via query params
  - Returns SearchV2Response (web results only; SearXNG doesn't expose images/news
    in the same structured way unless extra engines are configured)
"""
from __future__ import annotations

from typing import List, Optional

import httpx
import structlog

from app.config import settings
from app.models.search import SearchV2Response, WebSearchResult

log = structlog.get_logger(__name__)

_RESULTS_PER_PAGE = 20  # SearXNG default page size


class SearXNGScraper:
    """
    Calls a self-hosted SearXNG instance.
    Configure SEARXNG_ENDPOINT in .env, e.g. http://localhost:8080
    """

    def __init__(self, endpoint: Optional[str] = None) -> None:
        raw = endpoint or settings.searxng_endpoint or ""
        self._base_url = raw.rstrip("/") + "/search"

    # ── Public API ────────────────────────────────────────────────────────────

    async def search(
        self,
        query: str,
        *,
        num_results: int = 10,
        lang: Optional[str] = None,
        tbs: Optional[str] = None,
        page: int = 1,
    ) -> SearchV2Response:
        """
        Fetch up to `num_results` web results from SearXNG.

        Mirrors the TypeScript searxng_search() function:
          - Calculates how many pages are needed (ceil(num_results / 20))
          - Fetches pages sequentially, stops early on empty page
          - Trims result list to exactly num_results
        """
        if num_results <= 0:
            return SearchV2Response()

        pages_needed = max(1, -(-num_results // _RESULTS_PER_PAGE))  # ceil division
        web_results: List[WebSearchResult] = []

        for page_offset in range(pages_needed):
            page_results = await self._fetch_page(
                query=query,
                page=page + page_offset,
                lang=lang,
                tbs=tbs,
            )
            if not page_results:
                break
            web_results.extend(page_results)
            if len(web_results) >= num_results:
                break

        if not web_results:
            return SearchV2Response()

        return SearchV2Response(web=web_results[:num_results])

    # ── Internal ──────────────────────────────────────────────────────────────

    async def _fetch_page(
        self,
        query: str,
        page: int,
        lang: Optional[str],
        tbs: Optional[str],
    ) -> List[WebSearchResult]:
        params: dict = {
            "q": query,
            "pageno": page,
            "format": "json",
        }
        if lang:
            params["language"] = lang
        if settings.searxng_engines:
            params["engines"] = settings.searxng_engines
        if settings.searxng_categories:
            params["categories"] = settings.searxng_categories
        # SearXNG doesn't have a direct tbs equivalent, but we append it to the
        # query string so users can pass Google-style time filters if their
        # SearXNG instance uses Google as a backend.
        if tbs:
            params["q"] = f"{query} tbs:{tbs}"

        log.debug("searxng_fetch_page", url=self._base_url, page=page, query=query)

        try:
            async with httpx.AsyncClient(timeout=30) as client:
                resp = await client.get(
                    self._base_url,
                    params=params,
                    headers={"Content-Type": "application/json"},
                )
                resp.raise_for_status()
                data = resp.json()
        except httpx.HTTPStatusError as exc:
            log.warning(
                "searxng_http_error",
                status=exc.response.status_code,
                query=query,
            )
            return []
        except Exception as exc:
            log.error("searxng_fetch_failed", query=query, error=str(exc))
            return []

        raw_results = data.get("results") if isinstance(data, dict) else None
        if not isinstance(raw_results, list):
            return []

        results: List[WebSearchResult] = []
        for item in raw_results:
            url = item.get("url", "")
            if not url:
                continue
            results.append(
                WebSearchResult(
                    url=url,
                    title=item.get("title", ""),
                    description=item.get("content", ""),
                )
            )

        log.info("searxng_page_parsed", page=page, count=len(results))
        return results
