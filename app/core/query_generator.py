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

    # ── Generic web queries ───────────────────────────────────────────────────
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
        # Fallback: use raw keywords
        kws = " ".join(parsed.keywords[:4])
        queries += [f"{kws} contact India", f"{kws} phone email India"]

    # ── Directory-specific queries ────────────────────────────────────────────
    if industry:
        queries += [
            f'site:indiamart.com "{industry}"{loc_suffix}',
            f'site:justdial.com {industry}{loc_suffix}',
            f'site:yellowpages.in {industry}{loc_suffix}',
        ]

    # ── Intent-aware variants ─────────────────────────────────────────────────
    if parsed.intent == "B2B":
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
