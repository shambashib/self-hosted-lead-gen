"""
Lead API routes.

POST /api/leads/generate   — kick off a lead gen job (async)
GET  /api/leads/{job_id}   — get leads for a completed job
GET  /api/leads/export/csv — download all leads as CSV
"""
from __future__ import annotations

import csv
import io
from typing import List, Optional

from fastapi import APIRouter, BackgroundTasks, HTTPException, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from app.core.pipeline import LeadGenPipeline
from app.core.search_pipeline import SearchBasedLeadPipeline
from app.models.job import LeadJob, JobStatus
from app.models.lead import Lead
from app.storage.database import store

router = APIRouter(prefix="/api/leads", tags=["leads"])


class GenerateRequest(BaseModel):
    prompt: str
    min_score: Optional[int] = None
    # Firecrawl-style search parameters
    query: Optional[str] = None  # Direct search query (overrides prompt parsing)
    limit: Optional[int] = 10
    sources: Optional[List[str]] = ["web"]
    exclude_domains: Optional[List[str]] = None
    include_domains: Optional[List[str]] = None
    lang: Optional[str] = "en"
    country: Optional[str] = "in"
    location: Optional[str] = None


class GenerateResponse(BaseModel):
    job_id: str
    status: str
    message: str


class LeadsResponse(BaseModel):
    job_id: str
    status: str
    total: int
    leads: List[Lead]


# ── POST /generate ─────────────────────────────────────────────────────────

@router.post("/generate", response_model=GenerateResponse, status_code=202)
async def generate_leads(req: GenerateRequest, background_tasks: BackgroundTasks):
    """Start async lead generation. Returns job_id to poll status."""
    if not req.prompt or len(req.prompt.strip()) < 5:
        raise HTTPException(400, "Prompt is too short.")

    job = LeadJob(prompt=req.prompt.strip())
    db = store()
    await db.save_job(job)

    pipeline = LeadGenPipeline()
    background_tasks.add_task(pipeline.run, job)

    return GenerateResponse(
        job_id=job.id,
        status=JobStatus.pending.value,
        message="Job started. Poll GET /api/jobs/{job_id} for status.",
    )


# ── POST /generate/sync ────────────────────────────────────────────────────
@router.post("/generate/sync", response_model=LeadsResponse)
async def generate_leads_sync(req: GenerateRequest):
    """Synchronous lead generation — waits for completion (use for demos/testing).
    
    Supports two modes:
    1. Traditional: Uses directory scraping (JustDial, IndiaMART) based on prompt parsing
    2. Search-based: Uses DuckDuckGo/SearXNG search when query parameter is provided
    """
    if not req.prompt or len(req.prompt.strip()) < 5:
        raise HTTPException(400, "Prompt is too short.")

    # Use search-based pipeline if query is provided (Firecrawl-style)
    if req.query or (req.sources and "web" in req.sources):
        search_pipeline = SearchBasedLeadPipeline()
        job, leads = await search_pipeline.run(
            prompt=req.prompt.strip(),
            query=req.query,
            limit=req.limit or 10,
            sources=req.sources,
            exclude_domains=req.exclude_domains,
            include_domains=req.include_domains,
            lang=req.lang,
            country=req.country,
            location=req.location,
        )
    else:
        # Use traditional directory scraping pipeline
        job = LeadJob(prompt=req.prompt.strip())
        pipeline = LeadGenPipeline()
        job = await pipeline.run(job)

        db = store()
        leads = await db.get_leads_by_job(job.id)

    if req.min_score is not None:
        leads = [l for l in leads if l.score >= req.min_score]

    return LeadsResponse(
        job_id=job.id,
        status=job.status.value,
        total=len(leads),
        leads=leads,
    )


# ── GET /{job_id} ──────────────────────────────────────────────────────────

@router.get("/{job_id}", response_model=LeadsResponse)
async def get_leads(
    job_id: str,
    min_score: int = Query(default=0, ge=0, le=100),
    limit: int = Query(default=100, ge=1, le=1000),
    offset: int = Query(default=0, ge=0),
):
    db = store()
    job = await db.get_job(job_id)
    if not job:
        raise HTTPException(404, f"Job {job_id!r} not found.")

    leads = await db.get_leads_by_job(job_id)
    if min_score:
        leads = [l for l in leads if l.score >= min_score]

    leads = sorted(leads, key=lambda l: l.score, reverse=True)
    page = leads[offset: offset + limit]

    return LeadsResponse(job_id=job_id, status=job.status.value, total=len(leads), leads=page)


# ── GET /export/csv ────────────────────────────────────────────────────────

@router.get("/export/csv")
async def export_csv(
    job_id: Optional[str] = Query(default=None),
    min_score: int = Query(default=0),
):
    db = store()
    if job_id:
        leads = await db.get_leads_by_job(job_id)
    else:
        leads = await db.get_all_leads()

    if min_score:
        leads = [l for l in leads if l.score >= min_score]

    if not leads:
        raise HTTPException(404, "No leads found matching criteria.")

    output = io.StringIO()
    fieldnames = list(leads[0].to_csv_row().keys())
    writer = csv.DictWriter(output, fieldnames=fieldnames)
    writer.writeheader()
    for lead in leads:
        writer.writerow(lead.to_csv_row())

    output.seek(0)
    filename = f"leads_{job_id or 'all'}.csv"
    return StreamingResponse(
        iter([output.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
