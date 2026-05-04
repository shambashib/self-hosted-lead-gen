"""
Normalizer — cleans and standardises lead fields.
  • City name → canonical form
  • Phone → E.164 (best-effort)
  • Domain → website URL
  • Industry tag inference from business name / category
"""
from __future__ import annotations

import re
from typing import Optional
from urllib.parse import urlparse

import tldextract

from app.models.lead import Lead


# City aliases → canonical name
_CITY_ALIASES: dict[str, str] = {
    "bombay": "Mumbai", "new delhi": "Delhi", "ncr": "Delhi",
    "bengaluru": "Bangalore", "blr": "Bangalore",
    "calcutta": "Kolkata", "madras": "Chennai",
    "trivandrum": "Thiruvananthapuram", "ernakulam": "Kochi",
    "vizag": "Visakhapatnam", "prayagraj": "Allahabad",
    "gurugram": "Gurgaon", "gurgaon": "Gurgaon",
    "noida": "Noida",
}


def _normalise_city(city: Optional[str]) -> Optional[str]:
    if not city:
        return city
    lower = city.strip().lower()
    return _CITY_ALIASES.get(lower, city.strip().title())


def _normalise_phone(phone: Optional[str]) -> Optional[str]:
    if not phone:
        return phone
    digits = re.sub(r"\D", "", phone)
    if digits.startswith("91") and len(digits) == 12:
        return "+" + digits
    if digits.startswith("0") and len(digits) == 11:
        return "+91" + digits[1:]
    if len(digits) == 10 and digits[0] in "6789":
        return "+91" + digits
    return phone


def _infer_website(lead: Lead) -> Optional[str]:
    if lead.website:
        if not lead.website.startswith("http"):
            return "https://" + lead.website
        return lead.website

    if lead.email:
        domain = lead.email.split("@")[-1]
        ext = tldextract.extract(domain)
        if ext.domain and ext.suffix and ext.domain not in ("gmail", "yahoo", "hotmail", "outlook"):
            return f"https://{domain}"

    return None


def _infer_industry_tag(lead: Lead) -> Optional[str]:
    from app.core.prompt_parser import INDUSTRY_PATTERNS
    text = " ".join(filter(None, [lead.business_name, lead.category, " ".join(lead.tags)])).lower()
    for industry, keywords in INDUSTRY_PATTERNS.items():
        if any(kw in text for kw in keywords):
            return industry
    return None


def normalise(lead: Lead) -> Lead:
    lead.city = _normalise_city(lead.city)
    lead.phone = _normalise_phone(lead.phone)
    lead.website = _infer_website(lead)

    if not lead.industry:
        lead.industry = _infer_industry_tag(lead)

    # Ensure email is lowercase
    if lead.email:
        lead.email = lead.email.lower().strip()

    return lead
