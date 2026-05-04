"""
Storage backend — in-memory (default), MongoDB, or SQLite.

Switch via STORAGE_BACKEND env var.
"""
from __future__ import annotations

import json
import threading
from collections import defaultdict
from datetime import datetime
from typing import Dict, List, Optional, Any

import structlog

from app.config import StorageBackend, settings
from app.models.job import LeadJob
from app.models.lead import Lead

log = structlog.get_logger(__name__)


# ─── In-memory store (default, no deps) ──────────────────────────────────────

class InMemoryStore:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._leads: Dict[str, Lead] = {}
        self._jobs: Dict[str, LeadJob] = {}

    # Leads
    async def save_lead(self, lead: Lead) -> None:
        with self._lock:
            self._leads[lead.id] = lead

    async def save_leads(self, leads: List[Lead]) -> None:
        with self._lock:
            for lead in leads:
                self._leads[lead.id] = lead

    async def get_leads_by_job(self, job_id: str) -> List[Lead]:
        with self._lock:
            return [l for l in self._leads.values() if l.job_id == job_id]

    async def get_all_leads(self) -> List[Lead]:
        with self._lock:
            return list(self._leads.values())

    # Jobs
    async def save_job(self, job: LeadJob) -> None:
        with self._lock:
            self._jobs[job.id] = job

    async def get_job(self, job_id: str) -> Optional[LeadJob]:
        with self._lock:
            return self._jobs.get(job_id)

    async def list_jobs(self) -> List[LeadJob]:
        with self._lock:
            return sorted(self._jobs.values(), key=lambda j: j.created_at, reverse=True)


# ─── MongoDB store ────────────────────────────────────────────────────────────

class MongoStore:
    def __init__(self) -> None:
        from motor.motor_asyncio import AsyncIOMotorClient
        self._client = AsyncIOMotorClient(settings.mongodb_uri)
        self._db = self._client[settings.mongodb_db]
        self._leads_col = self._db["leads"]
        self._jobs_col = self._db["jobs"]

    async def save_lead(self, lead: Lead) -> None:
        doc = json.loads(lead.model_dump_json())
        await self._leads_col.update_one({"id": lead.id}, {"$set": doc}, upsert=True)

    async def save_leads(self, leads: List[Lead]) -> None:
        if not leads:
            return
        from pymongo import UpdateOne
        ops = [UpdateOne({"id": l.id}, {"$set": json.loads(l.model_dump_json())}, upsert=True) for l in leads]
        await self._leads_col.bulk_write(ops)

    async def get_leads_by_job(self, job_id: str) -> List[Lead]:
        docs = await self._leads_col.find({"job_id": job_id}).to_list(length=10000)
        return [Lead(**d) for d in docs]

    async def get_all_leads(self) -> List[Lead]:
        docs = await self._leads_col.find().to_list(length=100000)
        return [Lead(**d) for d in docs]

    async def save_job(self, job: LeadJob) -> None:
        doc = json.loads(job.model_dump_json())
        await self._jobs_col.update_one({"id": job.id}, {"$set": doc}, upsert=True)

    async def get_job(self, job_id: str) -> Optional[LeadJob]:
        doc = await self._jobs_col.find_one({"id": job_id})
        return LeadJob(**doc) if doc else None

    async def list_jobs(self) -> List[LeadJob]:
        docs = await self._jobs_col.find().sort("created_at", -1).to_list(length=1000)
        return [LeadJob(**d) for d in docs]


# ─── Factory ──────────────────────────────────────────────────────────────────

def get_store():
    if settings.storage_backend == StorageBackend.mongodb:
        try:
            return MongoStore()
        except Exception as e:
            log.warning("mongo_unavailable", error=str(e), fallback="memory")
    return InMemoryStore()


# Singleton
_store = None


def store():
    global _store
    if _store is None:
        _store = get_store()
    return _store
