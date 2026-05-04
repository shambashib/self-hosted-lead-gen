"""
Lead Scorer — assigns a quality score 0–100.

Scoring rubric:
  +30  has email
  +20  has phone
  +15  has website
  +10  has business name
  +5   has city
  +5   has address
  +5   has any social link
  +5   has industry tag
  +5   email is business domain (not gmail/yahoo/etc.)
  -10  no email AND no phone (low contact quality)
"""
from __future__ import annotations

from app.models.lead import Lead

_FREE_DOMAINS = {"gmail.com", "yahoo.com", "yahoo.in", "hotmail.com",
                 "outlook.com", "rediffmail.com", "ymail.com"}


def score(lead: Lead) -> int:
    s = 0

    if lead.email:
        s += 30
        domain = lead.email.split("@")[-1]
        if domain not in _FREE_DOMAINS:
            s += 5   # business email bonus
    if lead.phone:
        s += 20
    if lead.website:
        s += 15
    if lead.business_name:
        s += 10
    if lead.city:
        s += 5
    if lead.address:
        s += 5
    if lead.industry:
        s += 5
    if any([
        lead.social_links.linkedin,
        lead.social_links.twitter,
        lead.social_links.facebook,
        lead.social_links.instagram,
    ]):
        s += 5

    # Penalty: no usable contact at all
    if not lead.email and not lead.phone:
        s -= 10

    return max(0, min(100, s))


def score_all(leads: list) -> list:
    for lead in leads:
        lead.score = score(lead)
    return leads
