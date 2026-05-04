from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Dict, List, Optional
from uuid import uuid4

from pydantic import BaseModel, EmailStr, Field, field_validator


class SourceType(str, Enum):
    google_serp = "google_serp"
    indiamart = "indiamart"
    justdial = "justdial"
    company_website = "company_website"
    yellowpages = "yellowpages"
    unknown = "unknown"


class SocialLinks(BaseModel):
    linkedin: Optional[str] = None
    twitter: Optional[str] = None
    facebook: Optional[str] = None
    instagram: Optional[str] = None
    youtube: Optional[str] = None
    whatsapp: Optional[str] = None


class Lead(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid4()))
    job_id: str = ""

    # Core identity
    name: Optional[str] = None
    business_name: Optional[str] = None

    # Contact
    email: Optional[str] = None
    phone: Optional[str] = None
    phone_secondary: Optional[str] = None

    # Web presence
    website: Optional[str] = None
    social_links: SocialLinks = Field(default_factory=SocialLinks)

    # Location
    address: Optional[str] = None
    city: Optional[str] = None
    state: Optional[str] = None
    country: str = "India"
    pincode: Optional[str] = None

    # Classification
    category: Optional[str] = None
    tags: List[str] = Field(default_factory=list)
    industry: Optional[str] = None

    # Quality
    score: int = 0                          # 0-100
    is_verified: bool = False

    # Provenance
    source_type: SourceType = SourceType.unknown
    source_url: Optional[str] = None
    raw_snippet: Optional[str] = None

    # Timestamps
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    @field_validator("email", mode="before")
    @classmethod
    def normalise_email(cls, v: Optional[str]) -> Optional[str]:
        if v:
            return v.strip().lower()
        return v

    def to_csv_row(self) -> Dict:
        return {
            "id": self.id,
            "name": self.name or "",
            "business_name": self.business_name or "",
            "email": self.email or "",
            "phone": self.phone or "",
            "website": self.website or "",
            "city": self.city or "",
            "state": self.state or "",
            "address": self.address or "",
            "industry": self.industry or "",
            "tags": "|".join(self.tags),
            "score": self.score,
            "source_type": self.source_type.value,
            "source_url": self.source_url or "",
            "linkedin": self.social_links.linkedin or "",
            "twitter": self.social_links.twitter or "",
            "facebook": self.social_links.facebook or "",
            "instagram": self.social_links.instagram or "",
            "created_at": self.created_at.isoformat(),
        }
