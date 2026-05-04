from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Dict, List, Optional
from uuid import uuid4

from pydantic import BaseModel, Field


class JobStatus(str, Enum):
    pending = "pending"
    running = "running"
    completed = "completed"
    failed = "failed"
    partial = "partial"


class ParsedPrompt(BaseModel):
    raw: str
    industry: Optional[str] = None
    location: Optional[str] = None
    intent: str = "B2B"                      # B2B | B2C
    keywords: List[str] = Field(default_factory=list)
    entities: Dict[str, str] = Field(default_factory=dict)


class JobStats(BaseModel):
    queries_generated: int = 0
    urls_discovered: int = 0
    pages_crawled: int = 0
    leads_extracted: int = 0
    leads_after_dedup: int = 0
    leads_above_threshold: int = 0
    errors: int = 0


class LeadJob(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid4()))
    prompt: str
    parsed_prompt: Optional[ParsedPrompt] = None
    status: JobStatus = JobStatus.pending
    stats: JobStats = Field(default_factory=JobStats)
    error_message: Optional[str] = None
    lead_ids: List[str] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None

    @property
    def duration_seconds(self) -> Optional[float]:
        if self.started_at and self.completed_at:
            return (self.completed_at - self.started_at).total_seconds()
        return None
