"""
Source Classifier — categorises a URL into a known source type.
"""
from __future__ import annotations

from urllib.parse import urlparse

from app.models.lead import SourceType

_DIRECTORY_MAP = {
    "indiamart.com": SourceType.indiamart,
    "justdial.com": SourceType.justdial,
    "yellowpages.in": SourceType.yellowpages,
    "sulekha.com": SourceType.yellowpages,
    "tradeindia.com": SourceType.indiamart,
    "exportersindia.com": SourceType.indiamart,
}

_SKIP_DOMAINS = {
    "google.com", "google.co.in", "youtube.com", "facebook.com",
    "twitter.com", "instagram.com", "linkedin.com", "wikipedia.org",
    "amazon.in", "flipkart.com", "myntra.com", "nykaa.com",
    "quora.com", "reddit.com", "medium.com",
}


def classify(url: str) -> SourceType:
    try:
        host = urlparse(url).netloc.lower().lstrip("www.")
    except Exception:
        return SourceType.unknown

    if host in _SKIP_DOMAINS or any(host.endswith(f".{d}") for d in _SKIP_DOMAINS):
        return SourceType.unknown

    for domain, src_type in _DIRECTORY_MAP.items():
        if domain in host:
            return src_type

    return SourceType.company_website


def should_crawl(url: str) -> bool:
    return classify(url) != SourceType.unknown
