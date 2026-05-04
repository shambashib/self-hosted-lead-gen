"""
Job status API routes.

GET /api/jobs          — list all jobs
GET /api/jobs/{job_id} — get job status + stats
"""
from __future__ import annotations

from typing import List

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.models.job import LeadJob
from app.storage.database import store

router = APIRouter(prefix="/api/jobs", tags=["jobs"])


@router.get("", response_model=List[LeadJob])
async def list_jobs():
    db = store()
    return await db.list_jobs()


@router.get("/{job_id}", response_model=LeadJob)
async def get_job(job_id: str):
    db = store()
    job = await db.get_job(job_id)
    if not job:
        raise HTTPException(404, f"Job {job_id!r} not found.")
    return job
