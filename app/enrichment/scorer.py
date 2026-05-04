"""
Lead Scorer — assigns a quality score 0–100.

Scoring rubric:
  +30  has email
  +20  has phone
  +15  has website
  +10  has business name
  +10  sourced from a verified directory (IndiaMART / JustDial)
  +5   has LinkedIn profile link
  +5   has city
  +5   has address
  +5   has industry tag
  +5   email is a business domain (not gmail/yahoo/etc.)
  -5   no email AND no phone (partial penalty — directory listings are still useful)
"""
from __future__ import annotations

from app.models.lead import Lead, SourceType

_FREE_DOMAINS = {"gmail.com", "yahoo.com", "yahoo.in", "hotmail.com",
                 "outlook.com", "rediffmail.com", "ymail.com"}

_DIRECTORY_SOURCES = {SourceType.indiamart, SourceType.justdial, SourceType.yellowpages}


def score(lead: Lead) -> int:
    s = 0

    if lead.email:
        s += 30
        domain = lead.email.split("@")[-1]
        if domain not in _FREE_DOMAINS:
            s += 5       # business email bonus
    if lead.phone:
        s += 20
    if lead.website:
        s += 15
    if lead.business_name:
        s += 10
    if lead.source_type in _DIRECTORY_SOURCES:
        s += 10          # being listed in a directory is itself a quality signal
    if lead.social_links.linkedin:
        s += 5
    if lead.city:
        s += 5
    if lead.address:
        s += 5
    if lead.industry:
        s += 5
    if any([
        lead.social_links.twitter,
        lead.social_links.facebook,
        lead.social_links.instagram,
    ]):
        s += 3

    # Partial penalty: no contact info at all (reduced from -10 to -5 because
    # directory listings are still useful prospects even without visible contact)
    if not lead.email and not lead.phone:
        s -= 5

    return max(0, min(100, s))


def score_all(leads: list) -> list:
    for lead in leads:
        lead.score = score(lead)
    return leads
