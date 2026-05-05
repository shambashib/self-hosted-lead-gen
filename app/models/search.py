"""
Search models — mirrors Firecrawl's v2 search schema, ported to Pydantic v2.

SearchRequest   → validated inbound payload  (Zod searchRequestSchema equivalent)
SearchV2Response → structured result payload
SearchResponse  → full API response envelope
"""
from __future__ import annotations

from enum import Enum
from typing import Dict, List, Optional, Union
from uuid import uuid4

from pydantic import BaseModel, Field, field_validator, model_validator


# ── Result types ──────────────────────────────────────────────────────────────

class WebSearchResult(BaseModel):
    url: str
    title: str = ""
    description: str = ""
    category: Optional[str] = None   # populated by getCategoryFromUrl


class ImageSearchResult(BaseModel):
    url: str
    title: str = ""
    image_url: str = ""


class NewsSearchResult(BaseModel):
    url: str
    title: str = ""
    snippet: str = ""
    published_at: Optional[str] = None
    category: Optional[str] = None


class SearchV2Response(BaseModel):
    """Mirrors Firecrawl's SearchV2Response shape."""
    web: Optional[List[WebSearchResult]] = None
    images: Optional[List[ImageSearchResult]] = None
    news: Optional[List[NewsSearchResult]] = None


# ── Scrape options (subset relevant to search) ────────────────────────────────

class SearchScrapeOptions(BaseModel):
    """
    Controls post-search scraping of each result URL.
    Mirrors Firecrawl's scrapeOptions on the search endpoint.
    """
    formats: List[str] = Field(
        default_factory=list,
        description="Formats to extract: markdown, html, rawHtml, links",
    )

    @field_validator("formats")
    @classmethod
    def validate_formats(cls, v: List[str]) -> List[str]:
        allowed = {"markdown", "html", "rawHtml", "links"}
        bad = [f for f in v if f not in allowed]
        if bad:
            raise ValueError(f"Unsupported scrape formats: {bad}. Choose from {allowed}")
        return v


# ── Source & category enums ───────────────────────────────────────────────────

class SearchSource(str, Enum):
    web = "web"
    images = "images"
    news = "news"


class SearchCategory(str, Enum):
    github = "github"
    research = "research"
    pdf = "pdf"


# ── Inbound request schema ────────────────────────────────────────────────────

class SearchRequest(BaseModel):
    """
    Validated inbound payload for POST /api/v2/search.

    Mirrors Firecrawl's searchRequestSchema with self-hosted simplifications:
      - No billing / ZDR / agent-interop fields
      - country defaults to None (no forced "us" default)
    """
    query: str
    limit: int = Field(default=10, ge=1, le=100)
    tbs: Optional[str] = None        # time-based search filter, e.g. "qdr:d"
    filter: Optional[str] = None     # extra Google-style filter string
    lang: str = "en"
    country: Optional[str] = None
    location: Optional[str] = None

    sources: List[str] = Field(
        default_factory=lambda: ["web"],
        description="One or more of: web, images, news",
    )
    categories: Optional[List[str]] = Field(
        default=None,
        description="Optional category filters: github, research, pdf",
    )
    include_domains: Optional[List[str]] = None
    exclude_domains: Optional[List[str]] = None

    scrape_options: Optional[SearchScrapeOptions] = None
    timeout: int = Field(default=60000, ge=1000)   # milliseconds

    @field_validator("sources")
    @classmethod
    def validate_sources(cls, v: List[str]) -> List[str]:
        allowed = {"web", "images", "news"}
        bad = [s for s in v if s not in allowed]
        if bad:
            raise ValueError(f"Unknown sources: {bad}. Choose from {allowed}")
        return v

    @field_validator("categories")
    @classmethod
    def validate_categories(cls, v: Optional[List[str]]) -> Optional[List[str]]:
        if v is None:
            return v
        allowed = {"github", "research", "pdf"}
        bad = [c for c in v if c not in allowed]
        if bad:
            raise ValueError(f"Unknown categories: {bad}. Choose from {allowed}")
        return v

    @model_validator(mode="after")
    def check_domain_exclusivity(self) -> "SearchRequest":
        if self.include_domains and self.exclude_domains:
            raise ValueError("include_domains and exclude_domains cannot both be specified")
        return self


# ── API response envelope ─────────────────────────────────────────────────────

class SearchResponse(BaseModel):
    """Envelope returned by POST /api/v2/search — mirrors Firecrawl's shape."""
    success: bool
    data: SearchV2Response
    credits_used: int
    id: str = Field(default_factory=lambda: str(uuid4()))


# ── Internal context passed through the executor ─────────────────────────────

class SearchContext(BaseModel):
    """Non-public context threaded through execute_search (no billing fields needed)."""
    job_id: str
    origin: str = "api"
    timeout_seconds: float = 60.0


# ── Scrape result attached to each search item ────────────────────────────────

class ScrapedContent(BaseModel):
    """Content scraped from a single search result URL."""
    url: str
    markdown: Optional[str] = None
    html: Optional[str] = None
    raw_html: Optional[str] = None
    links: Optional[List[str]] = None
    error: Optional[str] = None
