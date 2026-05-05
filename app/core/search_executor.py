"""
Search Executor Service.

Python port of Firecrawl's executeSearch() TypeScript function, adapted for
the self-hosted lead-gen engine (no billing, no ZDR, no team-flags).

Public API:
    result = await execute_search(options, context, logger)

Key behaviours preserved from Firecrawl:
  - build_search_query()  — appends category site: modifiers and domain filters
  - get_category_from_url() — tags each result with its matched category
  - Trims web / images / news lists independently to `limit`
  - Post-search scraping when scrape_options.formats is non-empty
  - Parallel scraping via asyncio.gather (mirrors Promise.all)
  - Credit calculation: 2 credits per 10 results
"""
from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple
from urllib.parse import urlparse

import httpx
import structlog

from app.config import settings
from app.models.search import (
    ScrapedContent,
    SearchContext,
    SearchRequest,
    SearchScrapeOptions,
    SearchV2Response,
    WebSearchResult,
)
from app.scrapers.serp import make_serp_scraper
from app.scrapers.searxng import SearXNGScraper

log = structlog.get_logger(__name__)

# ── Category → site: modifier mapping ────────────────────────────────────────

_CATEGORY_SITE_MAP: Dict[str, List[str]] = {
    "github": ["github.com"],
    "research": ["arxiv.org", "researchgate.net", "scholar.google.com", "semanticscholar.org"],
    "pdf": [],  # handled via filetype:pdf modifier
}

_CATEGORY_DOMAIN_RE: Dict[str, re.Pattern] = {
    cat: re.compile("|".join(re.escape(d) for d in domains), re.IGNORECASE)
    for cat, domains in _CATEGORY_SITE_MAP.items()
    if domains
}


@dataclass
class SearchExecuteResult:
    response: SearchV2Response
    total_results_count: int
    search_credits: int
    scrape_credits: int
    total_credits: int
    should_scrape: bool


# ── Query builder (mirrors Firecrawl's buildSearchQuery) ─────────────────────

def build_search_query(
    query: str,
    categories: Optional[List[str]],
    include_domains: Optional[List[str]],
    exclude_domains: Optional[List[str]],
) -> Tuple[str, Dict[str, str]]:
    """
    Append site: / filetype: modifiers to the base query and return a
    category_map { domain_pattern → category_label } for result tagging.

    Returns: (modified_query, category_map)
    """
    parts = [query]
    category_map: Dict[str, str] = {}

    if categories:
        for cat in categories:
            if cat == "pdf":
                parts.append("filetype:pdf")
            else:
                sites = _CATEGORY_SITE_MAP.get(cat, [])
                if sites:
                    site_expr = " OR ".join(f"site:{s}" for s in sites)
                    parts.append(f"({site_expr})")
                    for domain in sites:
                        category_map[domain] = cat

    if include_domains:
        site_expr = " OR ".join(f"site:{d}" for d in include_domains)
        parts.append(f"({site_expr})")

    if exclude_domains:
        for d in exclude_domains:
            parts.append(f"-site:{d}")

    return " ".join(parts), category_map


def get_category_from_url(url: str, category_map: Dict[str, str]) -> Optional[str]:
    """Tag a result URL with its matched category label."""
    if not category_map:
        return None
    try:
        host = urlparse(url).netloc.lower()
    except Exception:
        return None
    for domain, cat in category_map.items():
        if domain in host:
            return cat
    return None


# ── Post-search scraping ──────────────────────────────────────────────────────

async def _scrape_url(url: str, formats: List[str], timeout: float) -> ScrapedContent:
    """
    Fetch a single URL and extract requested formats.

    Supported formats:
      - markdown  → plain text extracted from HTML (BeautifulSoup)
      - html      → cleaned inner text as HTML snippet
      - rawHtml   → raw response body
      - links     → all <a href> URLs found on the page
    """
    try:
        async with httpx.AsyncClient(
            timeout=timeout,
            follow_redirects=True,
            headers={"User-Agent": "Mozilla/5.0 (compatible; LeadGenBot/1.0)"},
        ) as client:
            resp = await client.get(url)
            resp.raise_for_status()
            raw_html = resp.text
    except Exception as exc:
        return ScrapedContent(url=url, error=str(exc))

    result = ScrapedContent(url=url)

    if "rawHtml" in formats:
        result.raw_html = raw_html

    if {"markdown", "html", "links"} & set(formats):
        try:
            from bs4 import BeautifulSoup
            soup = BeautifulSoup(raw_html, "lxml")

            # Remove script / style noise
            for tag in soup(["script", "style", "noscript"]):
                tag.decompose()

            if "html" in formats:
                body = soup.find("body")
                result.html = str(body) if body else raw_html[:50_000]

            if "markdown" in formats:
                # Simple text extraction — good enough for lead context
                result.markdown = soup.get_text(separator="\n", strip=True)[:20_000]

            if "links" in formats:
                result.links = [
                    a["href"]
                    for a in soup.find_all("a", href=True)
                    if a["href"].startswith("http")
                ]
        except Exception as exc:
            result.error = f"parse_error: {exc}"

    return result


async def scrape_search_results(
    urls: List[str],
    scrape_options: SearchScrapeOptions,
    timeout_seconds: float,
) -> List[ScrapedContent]:
    """
    Scrape all URLs in parallel (mirrors Firecrawl's Promise.all scraping).
    Respects MAX_CONCURRENT_CRAWLS from config.
    """
    if not urls:
        return []

    sem = asyncio.Semaphore(settings.max_concurrent_crawls)

    async def _bounded(url: str) -> ScrapedContent:
        async with sem:
            return await _scrape_url(url, scrape_options.formats, timeout_seconds)

    log.info("search_scrape_start", count=len(urls), formats=scrape_options.formats)
    results = await asyncio.gather(*[_bounded(u) for u in urls], return_exceptions=False)
    log.info("search_scrape_done", count=len(results))
    return list(results)


def _merge_scraped_content(
    response: SearchV2Response,
    scraped: List[ScrapedContent],
) -> None:
    """
    Attach scraped content back onto matching WebSearchResult items in-place.
    Mirrors Firecrawl's mergeScrapedContent().
    """
    scraped_map = {s.url: s for s in scraped}

    if response.web:
        for item in response.web:
            sc = scraped_map.get(item.url)
            if sc:
                # Attach as extra fields via model_extra (Pydantic v2 allows this
                # when model is configured with extra="allow"; otherwise we set them
                # as plain attributes since WebSearchResult is not strict here)
                if sc.markdown:
                    object.__setattr__(item, "markdown", sc.markdown)
                if sc.html:
                    object.__setattr__(item, "html", sc.html)
                if sc.raw_html:
                    object.__setattr__(item, "raw_html", sc.raw_html)
                if sc.links:
                    object.__setattr__(item, "links", sc.links)

    if response.news:
        for item in response.news:
            sc = scraped_map.get(item.url)
            if sc and sc.markdown:
                object.__setattr__(item, "markdown", sc.markdown)


# ── Credit calculation (mirrors Firecrawl's creditsPerTenResults logic) ───────

def calculate_search_credits(total_results: int) -> int:
    """2 credits per 10 results (rounded up)."""
    return max(1, -(-total_results // 10)) * 2  # ceil division × 2


# ── Main executor (mirrors executeSearch) ────────────────────────────────────

async def execute_search(
    options: SearchRequest,
    context: SearchContext,
    logger=None,
) -> SearchExecuteResult:
    """
    Core search execution — Python port of Firecrawl's executeSearch().

    Steps:
      1. Build query with category/domain modifiers
      2. Call search backend (SearXNG → Brave → Google)
      3. Tag results with category from URL
      4. Trim to `limit`
      5. Optionally scrape each result in parallel
      6. Calculate credits
    """
    if logger is None:
        logger = log

    num_results_buffer = options.limit * 2  # fetch 2× so we have room to filter

    logger.info("execute_search_start", query=options.query, limit=options.limit)

    # ── 1. Build query ────────────────────────────────────────────────────────
    search_query, category_map = build_search_query(
        query=options.query,
        categories=options.categories,
        include_domains=options.include_domains,
        exclude_domains=options.exclude_domains,
    )

    # ── 2. Call search backend ────────────────────────────────────────────────
    scraper = make_serp_scraper()
    timeout_seconds = options.timeout / 1000

    search_response: SearchV2Response

    if isinstance(scraper, SearXNGScraper):
        search_response = await scraper.search(
            search_query,
            num_results=num_results_buffer,
            lang=options.lang,
            tbs=options.tbs,
        )
    else:
        # Brave / Google SERP scrapers return List[SERPResult]; wrap into response
        from app.scrapers.serp import BraveSearchScraper
        raw_results = await scraper.search(search_query, num=num_results_buffer)
        web_items = [
            WebSearchResult(url=r.url, title=r.title, description=r.snippet)
            for r in raw_results
        ]
        search_response = SearchV2Response(web=web_items)

    # ── 3. Tag results with category ──────────────────────────────────────────
    if category_map:
        if search_response.web:
            for item in search_response.web:
                item.category = get_category_from_url(item.url, category_map)
        if search_response.news:
            for item in search_response.news:
                item.category = get_category_from_url(item.url, category_map)

    # ── 4. Trim to limit ──────────────────────────────────────────────────────
    total_results_count = 0

    if search_response.web:
        search_response.web = search_response.web[: options.limit]
        total_results_count += len(search_response.web)

    if search_response.images:
        search_response.images = search_response.images[: options.limit]
        total_results_count += len(search_response.images)

    if search_response.news:
        search_response.news = search_response.news[: options.limit]
        total_results_count += len(search_response.news)

    # ── 5. Post-search scraping ───────────────────────────────────────────────
    scrape_credits = 0
    should_scrape = bool(
        options.scrape_options
        and options.scrape_options.formats
    )

    if should_scrape and options.scrape_options:
        urls_to_scrape: List[str] = []

        if search_response.web:
            urls_to_scrape.extend(item.url for item in search_response.web)
        if search_response.news:
            urls_to_scrape.extend(item.url for item in search_response.news if item.url)

        if urls_to_scrape:
            scraped = await scrape_search_results(
                urls_to_scrape,
                options.scrape_options,
                timeout_seconds=min(timeout_seconds, 30),
            )
            _merge_scraped_content(search_response, scraped)
            scrape_credits = len([s for s in scraped if not s.error])

    # ── 6. Credits ────────────────────────────────────────────────────────────
    search_credits = calculate_search_credits(total_results_count)

    logger.info(
        "execute_search_done",
        total_results=total_results_count,
        search_credits=search_credits,
        scrape_credits=scrape_credits,
        should_scrape=should_scrape,
    )

    return SearchExecuteResult(
        response=search_response,
        total_results_count=total_results_count,
        search_credits=search_credits,
        scrape_credits=scrape_credits,
        total_credits=search_credits + scrape_credits,
        should_scrape=should_scrape,
    )
