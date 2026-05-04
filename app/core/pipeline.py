"""
Lead Generation Pipeline Orchestrator.

Flow:
  Prompt
    → parse_prompt()         [PromptParser]
    → generate_queries()     [QueryGenerator]
    → SERP scrape            [GoogleSERPScraper]
    → classify URLs          [SourceClassifier]
    → parallel crawl         [IndiaMARTCrawler | JustDialCrawler | WebsiteCrawler]
    → normalise()            [Normalizer]
    → deduplicate()          [Deduplicator]
    → score()                [Scorer]
    → filter by min score
    → persist                [Store]
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import List

import structlog

from app.config import settings
from app.core.prompt_parser import parse_prompt
from app.core.query_generator import generate_queries
from app.enrichment.deduplicator import deduplicate
from app.enrichment.normalizer import normalise
from app.enrichment.scorer import score_all
from app.models.job import JobStatus, LeadJob
from app.models.lead import Lead, SourceType
from app.queue.task_queue import run_tasks_concurrent
from app.scrapers.indiamart import IndiaMARTCrawler
from app.scrapers.justdial import JustDialCrawler
from app.scrapers.serp import GoogleSERPScraper
from app.scrapers.source_classifier import SourceType as ST, classify, should_crawl
from app.scrapers.website import WebsiteCrawler
from app.storage.database import store

log = structlog.get_logger(__name__)


class LeadGenPipeline:
    def __init__(self) -> None:
        self._serp = GoogleSERPScraper()
        self._indiamart = IndiaMARTCrawler()
        self._justdial = JustDialCrawler()
        self._website = WebsiteCrawler()

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
        log.info("prompt_parsed", industry=parsed.industry, location=parsed.location, intent=parsed.intent)

        # ── 2. Generate queries ────────────────────────────────────────────
        queries = generate_queries(parsed)[:settings.max_serp_queries]
        job.stats.queries_generated = len(queries)
        log.info("queries_generated", count=len(queries), queries=queries[:3])

        # ── 3. SERP scrape ─────────────────────────────────────────────────
        serp_coros = [
            self._serp.search(q, num=settings.serp_results_per_query)
            for q in queries
        ]
        serp_results_nested = await run_tasks_concurrent(serp_coros, concurrency=3)
        serp_results = [r for batch in serp_results_nested if batch for r in batch]

        # Collect all URLs, classify them
        url_map: dict[str, str] = {}   # url → source_type
        for r in serp_results:
            if should_crawl(r.url):
                url_map[r.url] = classify(r.url).value

        job.stats.urls_discovered = len(url_map)
        log.info("urls_discovered", count=len(url_map))

        all_leads: List[Lead] = []

        # ── 4a. Directory queries (IndiaMART, JustDial) ────────────────────
        directory_queries = [q for q in queries if "site:" not in q][:3]
        city = parsed.location or ""
        industry = parsed.industry or " ".join(parsed.keywords[:2])

        dir_coros = []
        for q in directory_queries:
            dir_coros.append(self._indiamart.search(q, job_id=job.id))
            dir_coros.append(self._justdial.search(q, city=city or "India", job_id=job.id))

        dir_results = await run_tasks_concurrent(dir_coros, concurrency=settings.max_concurrent_crawls)
        for batch in dir_results:
            if batch:
                all_leads.extend(batch)

        # ── 4b. Crawl discovered URLs ──────────────────────────────────────
        crawl_coros = []
        for url, src_type in list(url_map.items())[:20]:  # cap at 20 URLs
            if src_type == SourceType.company_website.value:
                crawl_coros.append(self._website.crawl(url, job_id=job.id))
            elif src_type == SourceType.indiamart.value:
                crawl_coros.append(self._indiamart.crawl_listing(url, job_id=job.id))

        crawl_results = await run_tasks_concurrent(crawl_coros, concurrency=settings.max_concurrent_crawls)
        for r in crawl_results:
            if r:
                all_leads.append(r)

        job.stats.pages_crawled = len(dir_coros) + len(crawl_coros)
        job.stats.leads_extracted = len(all_leads)
        log.info("leads_extracted", count=len(all_leads))

        # ── 5. Normalise ───────────────────────────────────────────────────
        all_leads = [normalise(l) for l in all_leads]

        # Tag with job metadata
        for lead in all_leads:
            lead.job_id = job.id
            if parsed.industry and parsed.industry not in lead.tags:
                lead.tags.append(parsed.industry)
            if parsed.location and parsed.location.lower() not in [t.lower() for t in lead.tags]:
                lead.tags.append(parsed.location)

        # ── 6. Deduplicate ─────────────────────────────────────────────────
        all_leads = deduplicate(all_leads)
        job.stats.leads_after_dedup = len(all_leads)

        # ── 7. Score ───────────────────────────────────────────────────────
        all_leads = score_all(all_leads)

        # ── 8. Filter by minimum score ─────────────────────────────────────
        all_leads = [l for l in all_leads if l.score >= settings.min_lead_score]
        log.info("leads_above_threshold", count=len(all_leads), threshold=settings.min_lead_score)

        # ── 9. Persist ─────────────────────────────────────────────────────
        db = store()
        await db.save_leads(all_leads)

        return all_leads
