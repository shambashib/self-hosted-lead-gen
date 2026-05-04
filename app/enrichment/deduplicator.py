"""
Deduplicator — merges leads that refer to the same business entity.

Strategy:
  1. Email-exact match → definite duplicate
  2. Phone-exact match → definite duplicate
  3. Business name fuzzy match + same city → probable duplicate (above threshold)
"""
from __future__ import annotations

import re
from typing import List

import structlog

from app.config import settings
from app.models.lead import Lead

log = structlog.get_logger(__name__)


def _normalise(s: str) -> str:
    s = s.lower()
    s = re.sub(r"[^a-z0-9\s]", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    for suffix in ["pvt ltd", "private limited", "ltd", "limited", "inc", "llp", "llc",
                   "co", "corp", "corporation", "enterprises", "solutions", "services"]:
        s = s.replace(suffix, "").strip()
    return s


def _token_overlap(a: str, b: str) -> float:
    ta = set(_normalise(a).split())
    tb = set(_normalise(b).split())
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / max(len(ta), len(tb))


def _is_duplicate(lead: Lead, seen: Lead) -> bool:
    # Exact email match
    if lead.email and seen.email and lead.email == seen.email:
        return True

    # Exact phone match
    if lead.phone and seen.phone:
        pa = re.sub(r"\D", "", lead.phone)[-10:]
        pb = re.sub(r"\D", "", seen.phone)[-10:]
        if pa and pb and pa == pb:
            return True

    # Fuzzy business name + city
    if lead.business_name and seen.business_name:
        score = _token_overlap(lead.business_name, seen.business_name)
        same_city = (
            not lead.city
            or not seen.city
            or lead.city.lower() == seen.city.lower()
        )
        if score >= settings.dedupe_threshold and same_city:
            return True

    return False


def _merge(primary: Lead, duplicate: Lead) -> Lead:
    """Merge fields from duplicate into primary, preferring non-null values."""
    def pick(a, b):
        return a if a is not None else b

    primary.email = pick(primary.email, duplicate.email)
    primary.phone = pick(primary.phone, duplicate.phone)
    primary.phone_secondary = pick(primary.phone_secondary, duplicate.phone)
    primary.website = pick(primary.website, duplicate.website)
    primary.address = pick(primary.address, duplicate.address)
    primary.city = pick(primary.city, duplicate.city)

    # Merge tags (unique)
    combined = list(dict.fromkeys(primary.tags + duplicate.tags))
    primary.tags = combined

    # Social links: fill missing fields
    for field in ("linkedin", "twitter", "facebook", "instagram", "youtube", "whatsapp"):
        if not getattr(primary.social_links, field):
            setattr(primary.social_links, field, getattr(duplicate.social_links, field))

    return primary


def deduplicate(leads: List[Lead]) -> List[Lead]:
    unique: List[Lead] = []
    for lead in leads:
        merged = False
        for existing in unique:
            if _is_duplicate(lead, existing):
                _merge(existing, lead)
                merged = True
                break
        if not merged:
            unique.append(lead)
    removed = len(leads) - len(unique)
    if removed:
        log.info("dedup_complete", original=len(leads), unique=len(unique), removed=removed)
    return unique
