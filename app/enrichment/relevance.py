"""
Relevance Filter — drops leads that clearly don't match the search intent.

Strategy:
  1. Positive match required: for known industries, the business name must contain
     at least one positive-signal keyword. "Royal Enfield Showroom" has zero
     skincare signals → dropped.
  2. Hard negatives: business name contains a known unrelated industry term → dropped.
  3. Entity filter: when targeting individuals, exclude large corporations/brands.
"""
from __future__ import annotations

import re
from typing import List, Optional

from app.models.job import ParsedPrompt
from app.models.lead import Lead

# At least one of these tokens must appear in business_name (or its URL slug)
# for the lead to pass for that industry.
_POSITIVE_SIGNALS: dict[str, set[str]] = {
    "skincare": {
        "skin", "skincare", "beauty", "cosmetic", "cosmetics", "organic",
        "derma", "dermatology", "aesthetic", "aesthetics", "serum", "cream",
        "lotion", "herbal", "natural", "wellness", "facial", "spa", "salon",
        "nykaa", "mamaearth", "wow", "minimalist", "plum", "dot", "mcaffeine",
        "lakme", "himalaya", "biotique", "lotus", "kama", "forest",
    },
    "real_estate": {
        "real", "estate", "property", "realty", "realtor", "builder",
        "developer", "construction", "housing", "homes", "infra", "projects",
        "residency", "heights", "towers", "enclave", "villa", "flat", "apartment",
    },
    "saas": {
        "software", "tech", "solutions", "systems", "cloud", "platform",
        "digital", "data", "ai", "analytics", "saas", "automation",
        "dev", "code", "app", "startup",
    },
    "healthcare": {
        "pharma", "pharmaceutical", "clinic", "hospital", "health", "medical",
        "medicine", "doctor", "care", "lab", "diagnostic", "wellness",
    },
    "ecommerce": {
        "store", "shop", "mart", "bazaar", "commerce", "brand", "online",
        "retail", "outlet", "market",
    },
    "food": {
        "food", "restaurant", "cafe", "kitchen", "bakery", "catering",
        "eatery", "dine", "cuisine", "chef",
    },
    "logistics": {
        "logistics", "freight", "shipping", "transport", "delivery",
        "courier", "cargo", "supply",
    },
    "manufacturing": {
        "manufacturing", "factory", "industries", "industrial", "production",
        "fabrication", "supplier", "exporter",
    },
    "finance": {
        "finance", "financial", "fintech", "investment", "capital", "fund",
        "insurance", "lending", "credit", "banking",
    },
    "consulting": {
        "consulting", "consultant", "advisory", "management", "strategy",
        "services", "solutions",
    },
}

_HARD_NEGATIVES: dict[str, set[str]] = {
    "skincare": {
        "enfield", "automobile", "automotive", "bike", "motorcycle",
        "footwear", "shoes", "skechers", "adidas", "nike",
        "electronics", "furniture", "grocery", "appliances", "jewellery",
    },
    "real_estate": {
        "restaurant", "food", "electronics", "automobile",
    },
    "saas": {
        "restaurant", "food", "grocery", "footwear",
    },
    "healthcare": {
        "automobile", "footwear", "electronics",
    },
}

# Known large corporations/brands to exclude when targeting individuals
_LARGE_CORPORATIONS = {
    "policybazaar", "bankbazaar", "hinduja hospital", "apollo hospital", "fortis",
    "max healthcare", "tata", "reliance", "adani", "mahindra", "l&t",
    "icici", "hdfc", "axis", "sbi", "kotak", "infosys", "tcs", "wipro",
    "flipkart", "amazon", "myntra", "ajio", "nykaa", "bigbasket",
    "zomato", "swiggy", "ola", "uber", "paytm", "phonepe", "gpay",
    "airtel", "jio", "vi", "bsnl", "star", "disney", "netflix",
    "government", "gov", "ministry", "department", "corporation",
    "hospital", "medical college", "institute", "university",
    # Insurance companies and platforms
    "aditya birla health", "aditya birla insurance", "icici prudential",
    "icici lombard", "hdfc life", "hdfc ergo", "axis bank", "kotak life",
    "kotak general", "sbi life", "max life", "max bupa", "tata aig",
    "reliance general", "bajaj allianz", "birla sun life", "aviva",
    "aegon religare", "dhfl pramerica", "edelweiss tokio", "future generali",
    "iffco tokio", "india first", "kotak mahindra", "lic", "life insurance",
    "general insurance", "star health", "royal sundaram", "cholamandalam",
    "oriental insurance", "new india assurance", "national insurance",
    "united india", "apollo munich", "cigna ttk", "health insurance",
    # Insurance comparison platforms
    "coverfox", "acko", "beshak", "ditto", "insurance articles",
    "policyx", "insurancedekho", "turtlemint", "zerodha", "groww",
}


def _tokens(text: str) -> set[str]:
    return set(re.findall(r"[a-z]{3,}", text.lower()))


def _slug_from_url(url: Optional[str]) -> str:
    """Extract readable text from a listing URL path."""
    if not url:
        return ""
    path = url.split("?")[0]          # drop query params
    path = path.rsplit("/", 2)[-2]    # second-to-last segment usually has business name
    return re.sub(r"[^a-z]", " ", path.lower())


def _is_large_corporation(lead: Lead) -> bool:
    """Check if the lead is a known large corporation/brand."""
    name_tokens = _tokens(lead.business_name or "")
    url_tokens = _tokens(_slug_from_url(lead.source_url))
    all_tokens = name_tokens | url_tokens
    
    # Check if any large corporation name appears in the business name or URL
    for corp in _LARGE_CORPORATIONS:
        if corp in " ".join(all_tokens):
            return True
    return False


def _is_insurance_provider(lead: Lead) -> bool:
    """Check if the lead is an insurance company or platform (not someone who needs insurance)."""
    name_lower = (lead.business_name or "").lower()
    url_lower = (lead.source_url or "").lower()
    
    # Insurance company indicators - expanded list
    insurance_provider_keywords = {
        "insurance", "insurer", "policy", "coverfox", "acko", "policybazaar",
        "bankbazaar", "beshak", "ditto", "insurancedekho", "policyx",
        "turtlemint", "prudential", "lombard", "ergo", "aig", "allianz",
        "aviva", "religare", "pramerica", "tokio", "generali", "assurance",
        "aditya birla", "icici", "hdfc", "axis", "kotak", "lic",
        "star health", "royal sundaram", "cholamandalam", "oriental",
        "new india", "national insurance", "united india", "apollo munich",
        "max life", "max bupa", "tata aig", "reliance general", "bajaj",
        "future generali", "iffco tokio", "edelweiss", "dhfl", "aegon",
    }
    
    # Check if business name contains insurance provider keywords
    for keyword in insurance_provider_keywords:
        if keyword in name_lower:
            return True
    
    # Check if business name ends with "Insurance" (common pattern)
    if name_lower.endswith(" insurance"):
        return True
    
    # Check if URL path contains insurance-related content (articles, comparison)
    if "insurance" in url_lower and any(x in url_lower for x in ["article", "compare", "quote", "policy"]):
        return True
    
    # Check if business name is clearly an insurance platform/content site
    if any(x in name_lower for x in ["article", "articles", "blog", "community", "org"]):
        if "insurance" in name_lower or "insurance" in url_lower:
            return True
    
    return False


def _is_relevant(lead: Lead, parsed: ParsedPrompt) -> bool:
    industry = parsed.industry or ""
    if not industry:
        return True   # no industry constraint → keep everything

    # ── Entity filter: exclude large corporations when targeting individuals ────
    if parsed.entity_type == "individual" and _is_large_corporation(lead):
        return False

    # ── Insurance provider filter: exclude insurance companies when looking for individuals who need insurance ────
    # This is critical: if we're looking for people who NEED insurance, exclude companies that SELL insurance
    is_insurance_search = (
        industry == "insurance" or
        "insurance" in parsed.raw.lower()
    )
    if is_insurance_search and _is_insurance_provider(lead):
        return False

    name_tokens = _tokens(lead.business_name or "")
    url_tokens  = _tokens(_slug_from_url(lead.source_url))
    all_tokens  = name_tokens | url_tokens

    # ── Hard negatives ────────────────────────────────────────────────────────
    negatives = _HARD_NEGATIVES.get(industry, set())
    if negatives and any(neg in name_tokens for neg in negatives):
        return False

    # ── Positive signal required ──────────────────────────────────────────────
    positives = _POSITIVE_SIGNALS.get(industry, set())
    if positives:
        # Check business name + URL slug — at least one positive must hit
        if not any(pos in all_tokens for pos in positives):
            return False

    return True


def filter_relevant(leads: List[Lead], parsed: ParsedPrompt) -> List[Lead]:
    before = len(leads)
    filtered = [l for l in leads if _is_relevant(l, parsed)]
    dropped = before - len(filtered)
    if dropped:
        import structlog
        logger = structlog.get_logger(__name__)
        logger.info(
            "relevance_filter", dropped=dropped, kept=len(filtered),
            entity_type=parsed.entity_type, industry=parsed.industry
        )
        # Log some examples of filtered leads for debugging
        dropped_leads = [l for l in leads if not _is_relevant(l, parsed)]
        if dropped_leads:
            logger.info(
                "filtered_leads_sample",
                samples=[l.business_name for l in dropped_leads[:3]]
            )
    return filtered
