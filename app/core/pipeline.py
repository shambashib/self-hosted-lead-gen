"""
Lead Generation Pipeline Orchestrator.

Flow:
  Prompt
    → parse_prompt()          [PromptParser]
    → generate_queries()      [QueryGenerator]
    → SERP scrape             [SearXNGScraper (self-hosted) | BraveSearchScraper (API) | GoogleSERPScraper (HTML fallback)]
    → classify URLs           [SourceClassifier]
    → parallel crawl          [IndiaMARTCrawler | JustDialCrawler | WebsiteCrawler]
    → LinkedIn company pages  [LinkedInScraper] (rate-limited, capped)
    → contact enrichment      [ContactEnricher] (phone from tel: links, email from company site)
    → set industry field      (from parsed prompt, not from query string)
    → normalise()             [Normalizer]
    → relevance filter        [RelevanceFilter]  ← removes irrelevant results
    → deduplicate()           [Deduplicator]
    → score()                 [Scorer]
    → filter by min score
    → persist                 [Store]
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import List

import structlog

from app.config import settings
from app.core.prompt_parser import parse_prompt
from app.core.query_generator import generate_queries
from app.enrichment.contact_enricher import enrich_leads
from app.enrichment.deduplicator import deduplicate
from app.enrichment.normalizer import normalise
from app.enrichment.relevance import filter_relevant
from app.enrichment.scorer import score_all
from app.models.job import JobStatus, LeadJob
from app.models.lead import Lead, SourceType
from app.queue.task_queue import run_tasks_concurrent
from app.scrapers.indiamart import IndiaMARTCrawler
from app.scrapers.justdial import JustDialCrawler
from app.scrapers.linkedin import LinkedInScraper
from app.core.search_executor import build_search_query
from app.scrapers.searxng import SearXNGScraper
from app.scrapers.serp import SERPResult, make_serp_scraper
from app.scrapers.source_classifier import classify, should_crawl
from app.scrapers.website import WebsiteCrawler
from app.storage.database import store

log = structlog.get_logger(__name__)


class LeadGenPipeline:
    def __init__(self) -> None:
        self._serp = make_serp_scraper()
        self._indiamart = IndiaMARTCrawler()
        self._justdial = JustDialCrawler()
        self._website = WebsiteCrawler()
        self._linkedin = LinkedInScraper()

    async def _search_query(
        self,
        query: str,
        *,
        num: int,
        lang: str = "en",
        categories: list | None = None,
        include_domains: list | None = None,
        exclude_domains: list | None = None,
    ) -> list:
        """
        Build query with Firecrawl-style modifiers then call whichever SERP
        backend is active. Normalises both SearXNG and Brave/Google responses
        into a flat List[SERPResult] so the rest of the pipeline is unchanged.
        """
        built_query, _ = build_search_query(query, categories, include_domains, exclude_domains)

        if isinstance(self._serp, SearXNGScraper):
            resp = await self._serp.search(built_query, num_results=num, lang=lang)
            return [
                SERPResult(url=item.url, title=item.title, snippet=item.description)
                for item in (resp.web or [])
            ]
        # Brave / Google — already return List[SERPResult]
        return await self._serp.search(built_query, num=num)

    async def run(self, job: LeadJob) -> LeadJob:
        db = store()
        job.status = JobStatus.running
        job.started_at = datetime.now(timezone.utc)
        await db.save_job(job)

        try:
            leads = await self._execute(job)
            job.lead_ids = [l.id for l in leads]
            job.stats.leads_above_threshold = len(leads)
            job.status = JobStatus.completed
        except Exception as exc:
            log.error("pipeline_error", job_id=job.id, error=str(exc))
            job.status = JobStatus.failed
            job.error_message = str(exc)
        finally:
            job.completed_at = datetime.now(timezone.utc)
            await db.save_job(job)

        return job

    async def _execute(self, job: LeadJob) -> List[Lead]:
        # ── 1. Parse prompt ────────────────────────────────────────────────
        parsed = await parse_prompt(job.prompt)
        job.parsed_prompt = parsed
        log.info("prompt_parsed", industry=parsed.industry, location=parsed.location, intent=parsed.intent, entity_type=parsed.entity_type, keywords=parsed.keywords)

        # Force insurance industry if prompt contains insurance-related terms
        if "insurance" in job.prompt.lower():
            parsed.industry = "insurance"
            log.info("industry_overridden_to_insurance", original=parsed.industry)

        industry = parsed.industry or " ".join(parsed.keywords[:2])
        city = parsed.location or ""

        # ── 2. Generate queries ────────────────────────────────────────────
        queries = generate_queries(parsed)[:settings.max_serp_queries]
        job.stats.queries_generated = len(queries)
        log.info("queries_generated", count=len(queries), queries=queries[:3])

        # ── 3. SERP scrape ─────────────────────────────────────────────────
        # Each query goes through build_search_query() (Firecrawl workflow):
        # appends site:/filetype: modifiers for categories and domain filters.
        serp_coros = [
            self._search_query(
                q,
                num=settings.serp_results_per_query,
                lang=job.lang,
                categories=job.categories,
                include_domains=job.include_domains,
                exclude_domains=job.exclude_domains,
            )
            for q in queries
        ]
        serp_results_nested = await run_tasks_concurrent(serp_coros, concurrency=3)
        serp_results = [r for batch in serp_results_nested if batch for r in batch]

        url_map: dict[str, str] = {}
        for r in serp_results:
            if should_crawl(r.url):
                url_map[r.url] = classify(r.url).value

        job.stats.urls_discovered = len(url_map)
        log.info("urls_discovered", count=len(url_map))

        all_leads: List[Lead] = []

        # ── 4a. Directory crawlers (IndiaMART + JustDial) ──────────────────
        # Use the 3 most targeted queries (exclude site: queries)
        directory_queries = [q for q in queries if "site:" not in q][:3]

        dir_coros = []
        for q in directory_queries:
            dir_coros.append(self._indiamart.search(q, job_id=job.id))
            dir_coros.append(self._justdial.search(q, city=city or "India", job_id=job.id))

        dir_results = await run_tasks_concurrent(dir_coros, concurrency=settings.max_concurrent_crawls)
        for batch in dir_results:
            if batch:
                all_leads.extend(batch)

        # ── 4b. Crawl company websites discovered via SERP ─────────────────
        crawl_coros = []
        for url, src_type in list(url_map.items())[:20]:
            if src_type == SourceType.company_website.value:
                crawl_coros.append(self._website.crawl(url, job_id=job.id))
            elif src_type == SourceType.indiamart.value:
                crawl_coros.append(self._indiamart.crawl_listing(url, job_id=job.id))

        crawl_results = await run_tasks_concurrent(crawl_coros, concurrency=settings.max_concurrent_crawls)
        for r in crawl_results:
            if r:
                all_leads.append(r)

        job.stats.pages_crawled = len(dir_coros) + len(crawl_coros)

        # ── 4c. LinkedIn company pages (rate-limited, capped) ──────────────
        if settings.linkedin_enabled:
            li_query = f"{industry} {city}".strip()
            try:
                li_leads = await self._linkedin.search(li_query, job_id=job.id)
                all_leads.extend(li_leads)
                log.info("linkedin_added", count=len(li_leads))
            except Exception as exc:
                log.warning("linkedin_failed", error=str(exc))

        job.stats.leads_extracted = len(all_leads)
        log.info("leads_extracted", count=len(all_leads))

        # ── 4d. Enrich contact info from individual listing pages ──────────
        all_leads = await enrich_leads(all_leads, max_enrich=15)

        # ── 5. Set industry from parsed prompt (not from query string) ──────
        for lead in all_leads:
            lead.job_id = job.id
            # Always use the parsed industry from the prompt, not what scrapers set
            lead.industry = industry
            # Clear any industry-related tags from scrapers and use the correct one
            lead.tags = [t for t in lead.tags if t not in ["healthcare", "health", "finance", "insurance"]]
            # Append location and industry as tags
            if parsed.industry and parsed.industry not in lead.tags:
                lead.tags.append(parsed.industry)
            if city and city.lower() not in [t.lower() for t in lead.tags]:
                lead.tags.append(city)

        # ── 6. Normalise ───────────────────────────────────────────────────
        all_leads = [normalise(l) for l in all_leads]

        # ── 7. Relevance filter — drop clearly off-topic results ───────────
        all_leads = filter_relevant(all_leads, parsed)

        # ── 8. Deduplicate ─────────────────────────────────────────────────
        all_leads = deduplicate(all_leads)
        job.stats.leads_after_dedup = len(all_leads)

        # ── 9. Score ───────────────────────────────────────────────────────
        all_leads = score_all(all_leads)

        # ── 10. Filter by minimum score ────────────────────────────────────
        all_leads = [l for l in all_leads if l.score >= settings.min_lead_score]
        log.info("leads_above_threshold", count=len(all_leads), threshold=settings.min_lead_score)

        # ── 11. Persist ────────────────────────────────────────────────────
        db = store()
        await db.save_leads(all_leads)

        return all_leads
