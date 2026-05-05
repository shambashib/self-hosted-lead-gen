"""
SearXNG Search Integration - DuckDuckGo-based search for lead generation.

This module implements search functionality using SearXNG (which uses DuckDuckGo)
similar to Firecrawl's search approach.
"""
from __future__ import annotations

import asyncio
from typing import List, Optional
from urllib.parse import quote_plus

import aiohttp
import structlog

from app.config import settings

log = structlog.get_logger(__name__)


class WebSearchResult:
    """Represents a single web search result."""
    def __init__(
        self,
        url: str,
        title: str,
        description: str,
    ):
        self.url = url
        self.title = title
        self.description = description


class SearXNGSearcher:
    """SearXNG-based web search using DuckDuckGo."""
    
    def __init__(self):
        # Use DuckDuckGo directly if SearXNG endpoint is not configured
        self.base_url = getattr(settings, 'searxng_endpoint', None) or "https://html.duckduckgo.com/html/"
        self.use_duckduckgo = not hasattr(settings, 'searxng_endpoint')
    
    async def search(
        self,
        query: str,
        num_results: int = 10,
        lang: str = "en",
        country: str = "in",
        exclude_domains: Optional[List[str]] = None,
        include_domains: Optional[List[str]] = None,
    ) -> List[WebSearchResult]:
        """
        Perform web search using DuckDuckGo via SearXNG or directly.
        
        Args:
            query: Search query string
            num_results: Number of results to return
            lang: Language code (e.g., "en")
            country: Country code (e.g., "in")
            exclude_domains: Domains to exclude from results
            include_domains: Domains to include (only these domains)
        
        Returns:
            List of WebSearchResult objects
        """
        if self.use_duckduckgo:
            return await self._search_duckduckgo(
                query, num_results, lang, country, exclude_domains, include_domains
            )
        else:
            return await self._search_searxng(
                query, num_results, lang, country, exclude_domains, include_domains
            )
    
    async def _search_duckduckgo(
        self,
        query: str,
        num_results: int,
        lang: str,
        country: str,
        exclude_domains: Optional[List[str]],
        include_domains: Optional[List[str]],
    ) -> List[WebSearchResult]:
        """Search using DuckDuckGo HTML interface."""
        results = []
        
        try:
            params = {
                "q": query,
                "kl": f"{lang}-{country}",
            }
            
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    self.base_url,
                    params=params,
                    headers={
                        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
                        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
                        "Accept-Language": "en-US,en;q=0.5",
                        "Accept-Encoding": "gzip, deflate",
                        "Connection": "keep-alive",
                        "Upgrade-Insecure-Requests": "1"
                    },
                    timeout=aiohttp.ClientTimeout(total=30)
                ) as response:
                    if response.status not in (200, 202):
                        log.error("duckduckgo_search_failed", status=response.status)
                        return []
                    
                    if response.status == 202:
                        log.info("duckduckgo_search_accepted", status=response.status)
                    
                    html = await response.text()
                    
                    # Log HTML for debugging
                    log.debug("duckduckgo_html_length", length=len(html))
                    if len(html) < 5000:
                        log.debug("duckduckgo_html_preview", html_preview=html[:1000])
                    
                    # Parse HTML to extract results
                    from bs4 import BeautifulSoup
                    soup = BeautifulSoup(html, 'lxml')
                    
                    # Try multiple selectors for DuckDuckGo results
                    result_divs = soup.select('div.result') or soup.select('div.web-result') or soup.select('article.result') or soup.select('[data-result]')
                    
                    # Log what we found
                    log.debug("duckduckgo_result_divs_found", count=len(result_divs))
                    
                    # If no results found with standard selectors, try finding all links
                    if not result_divs:
                        all_links = soup.find_all('a', href=True)
                        log.debug("duckduckgo_all_links_found", count=len(all_links))
                        # Filter links that look like search results (have http/https URLs)
                        result_divs = [link.parent for link in all_links if link.get('href', '').startswith(('http://', 'https://')) and 'duckduckgo' not in link.get('href', '')]
                        log.debug("duckduckgo_fallback_links", count=len(result_divs))
                    
                    for div in result_divs[:num_results]:
                        try:
                            # Extract URL - try multiple selectors
                            a_tag = div.select_one('a.result__a') or div.select_one('a[href]') or div.find('a')
                            if not a_tag:
                                continue
                            url = a_tag.get('href', '')
                            
                            # Decode DuckDuckGo redirect URLs
                            if url.startswith('/l/?uddg='):
                                from urllib.parse import unquote, urlparse, parse_qs
                                # Extract the uddg parameter
                                parsed = urlparse(url)
                                uddg = parse_qs(parsed.query).get('uddg', [''])[0]
                                url = unquote(uddg) if uddg else url
                            
                            # Extract title
                            title = a_tag.get_text(strip=True)
                            
                            # Extract description - try multiple selectors
                            desc_tag = div.select_one('a.result__snippet') or div.select_one('.result__snippet') or div.select_one('.snippet')
                            description = desc_tag.get_text(strip=True) if desc_tag else ""
                            
                            # Domain filtering
                            if exclude_domains or include_domains:
                                from urllib.parse import urlparse
                                parsed_url = urlparse(url)
                                domain = parsed_url.netloc.lower()
                                
                                if exclude_domains and any(excl in domain for excl in exclude_domains):
                                    continue
                                
                                if include_domains and not any(incl in domain for incl in include_domains):
                                    continue
                            
                            if url and title:
                                results.append(WebSearchResult(
                                    url=url,
                                    title=title,
                                    description=description
                                ))
                                
                        except Exception as e:
                            log.debug("duckduckgo_result_parse_error", error=str(e))
                            continue
                    
                    log.info("duckduckgo_search_completed", query=query, results=len(results))
                    
        except Exception as e:
            log.error("duckduckgo_search_error", error=str(e))
        
        return results
    
    async def _search_searxng(
        self,
        query: str,
        num_results: int,
        lang: str,
        country: str,
        exclude_domains: Optional[List[str]],
        include_domains: Optional[List[str]],
    ) -> List[WebSearchResult]:
        """Search using SearXNG API."""
        results = []
        
        try:
            params = {
                "q": query,
                "language": lang,
                "format": "json",
                "engines": "duckduckgo",
            }
            
            url = self.base_url.rstrip('/') + "/search"
            
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    url,
                    params=params,
                    headers={"Content-Type": "application/json"},
                    timeout=aiohttp.ClientTimeout(total=30)
                ) as response:
                    if response.status != 200:
                        log.error("searxng_search_failed", status=response.status)
                        return []
                    
                    data = await response.json()
                    
                    if data and isinstance(data, dict) and "results" in data:
                        for item in data["results"][:num_results]:
                            url = item.get("url", "")
                            title = item.get("title", "")
                            description = item.get("content", "")
                            
                            # Domain filtering
                            if exclude_domains or include_domains:
                                from urllib.parse import urlparse
                                parsed_url = urlparse(url)
                                domain = parsed_url.netloc.lower()
                                
                                if exclude_domains and any(excl in domain for excl in exclude_domains):
                                    continue
                                
                                if include_domains and not any(incl in domain for incl in include_domains):
                                    continue
                            
                            if url and title:
                                results.append(WebSearchResult(
                                    url=url,
                                    title=title,
                                    description=description
                                ))
                    
                    log.info("searxng_search_completed", query=query, results=len(results))
                    
        except Exception as e:
            log.error("searxng_search_error", error=str(e))
        
        return results


# Singleton instance
_searcher: Optional[SearXNGSearcher] = None


def get_searcher() -> SearXNGSearcher:
    """Get or create the singleton searcher instance."""
    global _searcher
    if _searcher is None:
        _searcher = SearXNGSearcher()
    return _searcher
