"""
Search-based Lead Generation Pipeline using Firecrawl-like approach.

This pipeline uses SearXNG/DuckDuckGo search to find leads instead of directory scraping.
It integrates with the existing prompt parsing and filtering logic.
"""
from __future__ import annotations

import asyncio
import re
from datetime import datetime, timezone
from typing import List, Optional
from urllib.parse import urlparse
from uuid import uuid4

import structlog

from app.config import settings
from app.core.prompt_parser import parse_prompt
from app.core.query_generator import generate_queries
from app.enrichment.contact_enricher import enrich_leads
from app.enrichment.relevance import filter_relevant
from app.enrichment.scorer import score_all
from app.models.job import JobStatus, LeadJob, ParsedPrompt
from app.models.lead import Lead, SourceType
from app.scrapers.serp import make_serp_scraper, SERPResult
from app.storage.database import store

log = structlog.get_logger(__name__)

_JOB_AND_CONTENT_DOMAINS = {
    "indeed.com", "simplyhired.com", "ziprecruiter.com", "jooble.org",
    "smartrecruiters.com", "greenhouse.io", "lever.co", "workable.com",
    "wellfound.com", "angel.co", "glassdoor.com", "monster.com",
    "careerbuilder.com", "builtinnyc.com", "builtin.com", "ladders.com",
    "quora.com", "reddit.com", "medium.com", "wikipedia.org", "ellty.com",
}
_JOB_AND_CONTENT_TERMS = {
    "job", "jobs", "hiring", "apply", "salary", "salaries", "career",
    "careers", "employment", "opening", "openings", "recruiting",
    "responsibilities", "how to hire", "what to expect", "best companies",
    "looking for", "now hiring", "urgent",
}
_PROFILE_DOMAINS = {
    "linkedin.com", "rocketreach.co", "apollo.io", "theorg.com",
    "crunchbase.com", "zoominfo.com",
}


class SearchBasedLeadPipeline:
    """Lead generation pipeline using web search (Firecrawl-style)."""
    
    def __init__(self):
        self._searcher = make_serp_scraper()
    
    async def run(
        self,
        prompt: str,
        query: Optional[str] = None,
        limit: int = 10,
        sources: Optional[List[str]] = None,
        exclude_domains: Optional[List[str]] = None,
        include_domains: Optional[List[str]] = None,
        lang: str = "en",
        country: str = "in",
        location: Optional[str] = None,
    ) -> tuple[LeadJob, List[Lead]]:
        """
        Execute search-based lead generation.
        
        Args:
            prompt: Natural language prompt for lead generation
            query: Direct search query (overrides prompt parsing if provided)
            limit: Number of leads to return
            sources: Search sources (currently only "web" supported)
            exclude_domains: Domains to exclude from search results
            include_domains: Domains to include (only these domains)
            lang: Language code
            country: Country code
            location: Location for search
        
        Returns:
            Tuple of (job, leads)
        """
        job = LeadJob(prompt=prompt)
        db = store()
        
        job.status = JobStatus.running
        job.started_at = datetime.now(timezone.utc)
        await db.save_job(job)
        
        try:
            leads = await self._execute(
                job,
                query=query,
                limit=limit,
                sources=sources,
                exclude_domains=exclude_domains,
                include_domains=include_domains,
                lang=lang,
                country=country,
                location=location,
            )
            
            job.lead_ids = [l.id for l in leads]
            job.stats.leads_above_threshold = len(leads)
            job.status = JobStatus.completed
            
        except Exception as exc:
            log.error("search_pipeline_error", job_id=job.id, error=str(exc))
            job.status = JobStatus.failed
            job.error_message = str(exc)
            leads = []
        
        finally:
            job.completed_at = datetime.now(timezone.utc)
            await db.save_job(job)
        
        return job, leads
    
    async def _execute(
        self,
        job: LeadJob,
        query: Optional[str] = None,
        limit: int = 10,
        sources: Optional[List[str]] = None,
        exclude_domains: Optional[List[str]] = None,
        include_domains: Optional[List[str]] = None,
        lang: str = "en",
        country: str = "in",
        location: Optional[str] = None,
    ) -> List[Lead]:
        """Execute the search-based lead generation."""
        
        db = store()
        
        # ── 1. Parse prompt ────────────────────────────────────────────────
        parsed = await parse_prompt(job.prompt)
        job.parsed_prompt = parsed
        log.info(
            "prompt_parsed",
            industry=parsed.industry,
            location=parsed.location,
            intent=parsed.intent,
            entity_type=parsed.entity_type,
            keywords=parsed.keywords,
        )
        
        # Override location if provided in request
        if location:
            parsed.location = location
        
        # Force insurance industry if prompt contains insurance-related terms
        if "insurance" in job.prompt.lower():
            parsed.industry = "insurance"
            log.info("industry_overridden_to_insurance", original=parsed.industry)
        
        # ── 2. Determine search query ────────────────────────────────────────
        if query:
            # Use direct query if provided
            search_query = query
        else:
            # Generate queries from parsed prompt
            queries = generate_queries(parsed)
            # Use the first generated query for search
            search_query = queries[0] if queries else parsed.keywords[0] if parsed.keywords else job.prompt

        if parsed.entity_type == "individual":
            search_query = self._people_search_query(search_query, parsed)
        
        # For insurance searches targeting individuals, use more specific queries
        # to avoid SME organizations/forums/databases
        if parsed.industry == "insurance" and parsed.entity_type == "individual":
            # Use queries that target actual business types, not "SME" which returns organizations
            search_query = search_query.replace("SME", "small business").replace("sme", "small business")
            search_query = search_query.replace("business owners", "restaurant owner OR retail shop owner OR consultant OR freelancer")
            log.info("refined_search_query_for_individuals", original=query or search_query, refined=search_query)
        
        log.info("search_query_selected", query=search_query)
        
        # ── 3. Perform web search ────────────────────────────────────────────
        search_results = await self._searcher.search(
            query=search_query,
            num=limit * 2,  # Get more results for filtering
        )
        
        log.info("search_completed", results=len(search_results))
        
        if not search_results:
            log.info("no_search_results_found")
            return []
        
        # ── 4. Convert search results to Lead objects ────────────────────────
        leads = self._search_results_to_leads(
            search_results,
            job_id=job.id,
            parsed=parsed,
        )
        
        log.info("leads_converted", count=len(leads))
        
        # ── 5. Enrich with contact information (emails, phones) ───────────────
        leads = await enrich_leads(leads, max_enrich=limit * 2)
        log.info("leads_after_contact_enrichment", count=len(leads))
        
        # ── 6. Apply relevance filtering ────────────────────────────────────
        leads = filter_relevant(leads, parsed)
        log.info("leads_after_relevance_filter", count=len(leads))
        
        # ── 7. Score leads ───────────────────────────────────────────────────
        leads = score_all(leads)
        
        # ── 8. Filter by minimum score ────────────────────────────────────────
        leads = [l for l in leads if l.score >= settings.min_lead_score]
        log.info("leads_above_threshold", count=len(leads), threshold=settings.min_lead_score)
        
        # ── 9. Limit results ───────────────────────────────────────────────────
        leads = leads[:limit]
        
        # ── 10. Persist leads ──────────────────────────────────────────────────
        await db.save_leads(leads)
        
        return leads

    def _people_search_query(self, query: str, parsed: ParsedPrompt) -> str:
        """Bias people-intent searches toward public profile pages, not job listings."""
        lower = query.lower()
        has_profile_site = any(site in lower for site in ["site:linkedin.com/in", "site:theorg.com"])
        negatives = "-jobs -job -hiring -careers -career -salary -indeed -simplyhired -ziprecruiter -jooble"
        if has_profile_site:
            return f"{query} {negatives}".strip()

        location = parsed.location or ""
        role_bits = " ".join(parsed.keywords[:6]) or query
        if parsed.industry and parsed.industry not in role_bits:
            role_bits = f"{role_bits} {parsed.industry}"
        if location and location.lower() not in role_bits.lower():
            role_bits = f"{role_bits} {location}"
        return f'site:linkedin.com/in "{role_bits}" {negatives}'.strip()
    
    def _search_results_to_leads(
        self,
        search_results: List[SERPResult],
        job_id: str,
        parsed: ParsedPrompt,
    ) -> List[Lead]:
        """Convert web search results to Lead objects."""
        leads = []
        
        for result in search_results:
            try:
                if self._should_skip_result(result, parsed):
                    continue

                # Extract business name from title
                business_name = result.title.strip()
                name = None
                socials = None
                if parsed.entity_type == "individual":
                    name, business_name, socials = self._extract_person_from_result(result)
                
                # Extract basic info
                lead = Lead(
                    job_id=job_id,
                    business_name=business_name,
                    name=name,
                    email=None,
                    phone=None,
                    website=result.url,
                    source_type=SourceType.company_website,
                    source_url=result.url,
                    raw_snippet=result.snippet,
                    industry=parsed.industry or "general",
                    tags=["web_search"],
                )
                if socials:
                    lead.social_links = socials
                
                # Add location if available
                if parsed.location:
                    lead.city = parsed.location
                    lead.tags.append(parsed.location)
                
                # Add industry as tag
                if parsed.industry:
                    lead.tags.append(parsed.industry)
                
                leads.append(lead)
                
            except Exception as e:
                log.debug("search_result_conversion_error", error=str(e), url=result.url)
                continue
        
        return leads

    def _should_skip_result(self, result: SERPResult, parsed: ParsedPrompt) -> bool:
        host = urlparse(result.url).netloc.lower().lstrip("www.")
        title = result.title.lower()
        snippet = result.snippet.lower()
        haystack = f"{title} {snippet} {result.url.lower()}"

        if any(host == d or host.endswith("." + d) for d in _JOB_AND_CONTENT_DOMAINS):
            return True
        if parsed.entity_type == "individual":
            if any(term in haystack for term in _JOB_AND_CONTENT_TERMS):
                return True
            is_profile = (
                "linkedin.com/in/" in result.url.lower()
                or any(host == d or host.endswith("." + d) for d in _PROFILE_DOMAINS)
            )
            if not is_profile:
                return True
        return False

    def _extract_person_from_result(self, result: SERPResult):
        from app.models.lead import SocialLinks

        title = re.sub(r"\s+", " ", result.title).strip()
        title = re.sub(r"\s*\|\s*LinkedIn.*$", "", title, flags=re.I)
        parts = [p.strip(" -") for p in re.split(r"\s[-|]\s", title) if p.strip(" -")]
        name = parts[0] if parts else None
        business_name = parts[-1] if len(parts) >= 3 else title

        if "linkedin.com/in/" in result.url.lower():
            socials = SocialLinks(linkedin=result.url.rstrip("/"))
        else:
            socials = SocialLinks()

        return name, business_name, socials
