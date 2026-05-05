"""
Search API route — POST /api/v2/search

Python port of Firecrawl's searchController, adapted for self-hosted use:
  - No auth middleware / credit-gate (self-hosted, single-tenant)
  - No country-check middleware (no RESTRICTED_COUNTRIES config)
  - No ZDR / agent-interop fields
  - Full request validation via SearchRequest (Pydantic)
  - Delegates to execute_search() — mirrors Firecrawl's executeSearch()
  - Returns { success, data, credits_used, id }
"""
from __future__ import annotations

from uuid import uuid4

import structlog
from fastapi import APIRouter, HTTPException
from pydantic import ValidationError

from app.core.search_executor import execute_search
from app.models.search import SearchContext, SearchRequest, SearchResponse, SearchV2Response

log = structlog.get_logger(__name__)

router = APIRouter(prefix="/api/v2", tags=["search"])


@router.post("/search", response_model=SearchResponse)
async def search_controller(req: SearchRequest) -> SearchResponse:
    """
    Execute a structured web search.

    Mirrors Firecrawl's POST /v2/search controller:
      - Validates the request body (Pydantic handles this automatically)
      - Builds and executes the search via execute_search()
      - Optionally scrapes each result URL when scrape_options.formats is set
      - Returns results with credit accounting

    Example request:
        POST /api/v2/search
        {
          "query": "SaaS companies in Bangalore",
          "limit": 10,
          "sources": ["web"],
          "categories": ["github"],
          "scrape_options": { "formats": ["markdown"] }
        }
    """
    job_id = str(uuid4())
    logger = log.bind(job_id=job_id, query=req.query)

    logger.info("search_request_received", limit=req.limit, sources=req.sources)

    context = SearchContext(
        job_id=job_id,
        origin="api",
        timeout_seconds=req.timeout / 1000,
    )

    try:
        result = await execute_search(req, context, logger)
    except Exception as exc:
        logger.error("search_controller_error", error=str(exc))
        raise HTTPException(status_code=500, detail=str(exc))

    logger.info(
        "search_request_done",
        total_results=result.total_results_count,
        credits=result.total_credits,
    )

    return SearchResponse(
        success=True,
        data=result.response,
        credits_used=result.total_credits,
        id=job_id,
    )
