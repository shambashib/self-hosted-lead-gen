"""
Query Generator — converts a ParsedPrompt into a ranked list of search queries
targeting Google SERP and specific business directories.
"""
from __future__ import annotations

from typing import List

from app.models.job import ParsedPrompt


def generate_queries(parsed: ParsedPrompt) -> List[str]:
    """Return up to 10 search queries derived from the parsed prompt."""
    industry = parsed.industry or " ".join(parsed.keywords[:3])
    location = parsed.location or ""
    loc_suffix = f" {location}" if location else ""

    queries: List[str] = []

    # ── SPECIAL CASE: Insurance searches for individuals ─────────────────────
    # When looking for individuals who NEED insurance, search for businesses in other industries
    # that would need insurance, NOT insurance companies themselves
    is_insurance_search = (
        industry == "insurance" or
        "insurance" in parsed.raw.lower()
    )

    if is_insurance_search and parsed.entity_type == "individual":
        # Search for businesses in other industries that would need insurance
        target_industries = [
            "restaurant", "retail shop", "manufacturing", "construction",
            "consulting", "freelancer", "doctor", "clinic", "trader",
            "small business", "startup", "professional", "contractor"
        ]
        for target in target_industries:
            if location:
                queries.append(f"{target} {location}")
                queries.append(f"{target} business owners {location}")
            else:
                queries.append(f"{target} India")
                queries.append(f"{target} business owners India")
    # ── Entity-aware query generation ─────────────────────────────────────────
    elif parsed.entity_type == "individual":
        raw_lower = parsed.raw.lower()
        is_executive_search = any(
            token in raw_lower
            for token in ["vp", "vice president", "director", "head of", "cxo", "ceo", "cto", "cro", "cmo"]
        )
        if is_executive_search:
            role_terms = " ".join(
                kw for kw in parsed.keywords
                if kw not in {"find", "people", "person", "individual"}
            ) or industry
            queries += [
                f'site:linkedin.com/in "{role_terms}"{loc_suffix} -jobs -hiring -careers',
                f'"{role_terms}" "{location}" "LinkedIn" -jobs -hiring' if location else f'"{role_terms}" "LinkedIn" -jobs -hiring',
                f'"VP Sales" "B2B SaaS"{loc_suffix} "LinkedIn" -jobs -hiring',
            ]
        # Target individual business owners, entrepreneurs, self-employed
        if industry and location:
            queries += [
                f"{industry} business owners {location}",
                f"{industry} entrepreneurs {location}",
                f"{industry} self employed {location}",
                f"{industry} consultants {location}",
                f"{industry} practitioners {location}",
                f"small {industry} business {location}",
            ]
        elif industry:
            queries += [
                f"{industry} business owners India",
                f"{industry} entrepreneurs India",
                f"{industry} self employed India",
                f"small {industry} business India",
            ]
        else:
            kws = " ".join(parsed.keywords[:4])
            queries += [f"{kws} business owners India", f"{kws} entrepreneurs India"]
    else:
        # Target companies, corporations, brands
        if industry and location:
            queries += [
                f"{industry} {location}",
                f"{industry} companies {location} contact",
                f"top {industry} brands {location}",
                f"{industry} {location} phone email",
                f"best {industry} {location} directory",
            ]
        elif industry:
            queries += [
                f"{industry} companies India contact",
                f"top {industry} brands India",
            ]
        else:
            kws = " ".join(parsed.keywords[:4])
            queries += [f"{kws} contact India", f"{kws} phone email India"]

    # ── Directory-specific queries ────────────────────────────────────────────
    if industry and not is_insurance_search:
        if parsed.entity_type == "individual":
            # For individuals, use more specific directory queries
            queries += [
                f'site:justdial.com "{industry} business owner"{loc_suffix}',
                f'site:justdial.com "{industry} consultant"{loc_suffix}',
                f'site:indiamart.com "{industry} individual"{loc_suffix}',
            ]
        else:
            queries += [
                f'site:indiamart.com "{industry}"{loc_suffix}',
                f'site:justdial.com {industry}{loc_suffix}',
                f'site:yellowpages.in {industry}{loc_suffix}',
            ]

    # ── Intent-aware variants ─────────────────────────────────────────────────
    if not is_insurance_search:
        if parsed.intent == "B2B":
            if parsed.entity_type == "individual":
                queries += [f"{industry} individual suppliers{loc_suffix}"]
            else:
                queries += [f"{industry} suppliers exporters{loc_suffix}"]
        else:
            queries += [f"{industry} shops{loc_suffix} buy online"]

    # Deduplicate while preserving order
    seen: set = set()
    unique: List[str] = []
    for q in queries:
        q = q.strip()
        if q and q not in seen:
            seen.add(q)
            unique.append(q)

    return unique[:10]
