"""
Prompt Parser — converts a natural-language lead-gen prompt into structured intent.

Strategy (in order of availability):
  1. LLM (OpenAI / Anthropic) if configured
  2. Rule-based NLP with keyword matching (zero-dependency fallback)
"""
from __future__ import annotations

import re
from typing import Optional

import structlog

from app.config import LLMProvider, settings
from app.models.job import ParsedPrompt

log = structlog.get_logger(__name__)

# ─── Geography ────────────────────────────────────────────────────────────────
INDIAN_CITIES = {
    "mumbai", "delhi", "bangalore", "bengaluru", "hyderabad", "chennai",
    "kolkata", "pune", "ahmedabad", "surat", "jaipur", "lucknow", "kanpur",
    "nagpur", "indore", "thane", "bhopal", "visakhapatnam", "vizag", "patna",
    "vadodara", "ghaziabad", "ludhiana", "agra", "nashik", "faridabad",
    "meerut", "rajkot", "kalyan", "vasai", "varanasi", "aurangabad",
    "dhanbad", "amritsar", "navi mumbai", "allahabad", "prayagraj", "ranchi",
    "howrah", "coimbatore", "jabalpur", "gwalior", "vijayawada", "jodhpur",
    "madurai", "raipur", "kota", "chandigarh", "guwahati", "solapur",
    "hubli", "dharwad", "tiruchirappalli", "trichy", "bareilly", "moradabad",
    "mysore", "mysuru", "tiruppur", "gurgaon", "gurugram", "noida",
    "aligarh", "jalandhar", "bhubaneswar", "salem", "mira bhayandar",
    "thiruvananthapuram", "trivandrum", "bhiwandi", "saharanpur", "gorakhpur",
    "guntur", "bikaner", "amravati", "noida", "jamshedpur", "bhilai",
    "warangul", "cuttack", "firozabad", "kochi", "ernakulam", "nellore",
    "s.a.s. nagar", "mohali", "dehradun",
}

INDIAN_STATES = {
    "andhra pradesh", "arunachal pradesh", "assam", "bihar", "chhattisgarh",
    "goa", "gujarat", "haryana", "himachal pradesh", "jharkhand", "karnataka",
    "kerala", "madhya pradesh", "maharashtra", "manipur", "meghalaya",
    "mizoram", "nagaland", "odisha", "punjab", "rajasthan", "sikkim",
    "tamil nadu", "telangana", "tripura", "uttar pradesh", "uttarakhand",
    "west bengal", "delhi", "jammu and kashmir", "ladakh",
}

# ─── Industry taxonomy ────────────────────────────────────────────────────────
INDUSTRY_PATTERNS = {
    "skincare": ["skincare", "skin care", "beauty", "cosmetics", "personal care"],
    "real_estate": ["real estate", "property", "realty", "realtor", "housing", "builder", "developer", "flat", "apartment"],
    "saas": ["saas", "software", "tech startup", "b2b software", "cloud", "platform"],
    "ecommerce": ["d2c", "direct to consumer", "ecommerce", "e-commerce", "online store", "brand"],
    "healthcare": ["healthcare", "health", "pharma", "pharmaceutical", "hospital", "clinic", "doctor"],
    "finance": ["finance", "fintech", "lending", "insurance", "investment", "banking"],
    "education": ["education", "edtech", "coaching", "training", "school", "college"],
    "food": ["food", "restaurant", "cafe", "fmcg", "beverage", "catering"],
    "manufacturing": ["manufacturing", "factory", "supplier", "exporter", "importer", "wholesale"],
    "logistics": ["logistics", "freight", "shipping", "transport", "delivery", "courier"],
    "hospitality": ["hotel", "resort", "hospitality", "travel", "tourism"],
    "retail": ["retail", "shop", "store", "outlet"],
    "consulting": ["consulting", "consultant", "advisory", "agency"],
    "marketing": ["marketing", "digital marketing", "advertising", "agency", "seo"],
    "recruitment": ["recruitment", "hr", "staffing", "talent"],
}

INTENT_B2C_SIGNALS = {"consumer", "customer", "individual", "person", "people", "buyer", "shopper"}
INTENT_B2B_SIGNALS = {"business", "company", "enterprise", "b2b", "corporate", "brand", "supplier", "vendor", "founder", "ceo", "startup"}

# Entity type signals - distinguish between individuals vs companies
ENTITY_INDIVIDUAL_SIGNALS = {
    "business owner", "business owners", "self-employed", "self employed", "freelancer", 
    "freelancers", "entrepreneur", "entrepreneurs", "sme", "sme's", "smes", "small business",
    "small businesses", "proprietor", "proprietors", "sole proprietor", "individual",
    "person", "professional", "professionals", "owner", "owners", "founder", "founders",
    "independent", "consultant", "consultants", "practitioner", "practitioners"
}
ENTITY_COMPANY_SIGNALS = {
    "company", "companies", "corporation", "corporations", "enterprise", "enterprises",
    "brand", "brands", "organization", "organizations", "firm", "firms", "agency",
    "agencies", "institution", "institutions", "hospital", "hospitals", "clinic",
    "clinics", "chain", "chains", "group", "groups", "conglomerate", "multinational"
}

# ─── Rule-based parser ────────────────────────────────────────────────────────

def _find_location(text: str) -> Optional[str]:
    lower = text.lower()
    for city in sorted(INDIAN_CITIES, key=len, reverse=True):
        if city in lower:
            return city.title()
    for state in sorted(INDIAN_STATES, key=len, reverse=True):
        if state in lower:
            return state.title()
    # Regex fallback: "in <Location>"
    m = re.search(r"\bin\s+([A-Z][a-zA-Z\s]{2,25})", text)
    if m:
        return m.group(1).strip()
    return None


def _find_industry(text: str) -> Optional[str]:
    lower = text.lower()
    for industry, keywords in INDUSTRY_PATTERNS.items():
        if any(kw in lower for kw in keywords):
            return industry
    return None


def _find_intent(text: str) -> str:
    lower = text.lower()
    b2c_hits = sum(1 for s in INTENT_B2C_SIGNALS if s in lower)
    b2b_hits = sum(1 for s in INTENT_B2B_SIGNALS if s in lower)
    return "B2C" if b2c_hits > b2b_hits else "B2B"


def _find_entity_type(text: str) -> str:
    """Detect if the prompt targets individuals or companies."""
    lower = text.lower()
    individual_hits = sum(1 for s in ENTITY_INDIVIDUAL_SIGNALS if s in lower)
    company_hits = sum(1 for s in ENTITY_COMPANY_SIGNALS if s in lower)
    return "individual" if individual_hits > company_hits else "company"


def _extract_keywords(text: str) -> list[str]:
    stopwords = {"find", "get", "search", "list", "show", "me", "i", "want", "need",
                 "in", "at", "for", "of", "the", "a", "an", "and", "or", "with"}
    words = re.findall(r"\b[a-zA-Z]{3,}\b", text.lower())
    return [w for w in words if w not in stopwords]


def _rule_based_parse(prompt: str) -> ParsedPrompt:
    return ParsedPrompt(
        raw=prompt,
        industry=_find_industry(prompt),
        location=_find_location(prompt),
        intent=_find_intent(prompt),
        entity_type=_find_entity_type(prompt),
        keywords=_extract_keywords(prompt),
    )


# ─── LLM-backed parser ────────────────────────────────────────────────────────

async def _llm_parse(prompt: str) -> ParsedPrompt:
    system = (
        "You are a lead generation assistant. Extract structured fields from the user prompt. "
        "Return ONLY valid JSON with keys: industry (string|null), location (string|null), "
        "intent ('B2B'|'B2C'), entity_type ('individual'|'company'), keywords (array of strings). "
        "entity_type should be 'individual' if the prompt targets business owners, entrepreneurs, "
        "self-employed professionals, freelancers, SMEs, or specific people. "
        "entity_type should be 'company' if the prompt targets companies, corporations, brands, "
        "or organizations."
    )
    try:
        if settings.llm_provider == LLMProvider.openai:
            import openai
            client = openai.AsyncOpenAI(api_key=settings.openai_api_key)
            resp = await client.chat.completions.create(
                model=settings.llm_model,
                messages=[{"role": "system", "content": system},
                          {"role": "user", "content": prompt}],
                response_format={"type": "json_object"},
                temperature=0,
            )
            import json
            data = json.loads(resp.choices[0].message.content)
        elif settings.llm_provider == LLMProvider.anthropic:
            import anthropic
            client = anthropic.AsyncAnthropic(api_key=settings.anthropic_api_key)
            msg = await client.messages.create(
                model=settings.llm_model,
                max_tokens=256,
                system=system,
                messages=[{"role": "user", "content": prompt}],
            )
            import json
            data = json.loads(msg.content[0].text)
        else:
            return _rule_based_parse(prompt)

        return ParsedPrompt(
            raw=prompt,
            industry=data.get("industry"),
            location=data.get("location"),
            intent=data.get("intent", "B2B"),
            entity_type=data.get("entity_type", "company"),
            keywords=data.get("keywords", []),
        )
    except Exception as exc:
        log.warning("llm_parse_failed", error=str(exc), fallback="rule_based")
        return _rule_based_parse(prompt)


# ─── Public API ───────────────────────────────────────────────────────────────

async def parse_prompt(prompt: str) -> ParsedPrompt:
    if settings.llm_provider != LLMProvider.none:
        return await _llm_parse(prompt)
    return _rule_based_parse(prompt)
