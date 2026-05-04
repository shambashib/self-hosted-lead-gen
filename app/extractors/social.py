"""
Social Link Extractor — finds public social media profile URLs from HTML.
Only harvests links present in the page markup; no authentication involved.
"""
from __future__ import annotations

import re
from typing import Optional
from urllib.parse import urljoin

from bs4 import BeautifulSoup

from app.models.lead import SocialLinks

_PATTERNS = {
    "linkedin":  re.compile(r"https?://(?:www\.)?linkedin\.com/(?:company|in)/[^\"'\s<>]+", re.I),
    "twitter":   re.compile(r"https?://(?:www\.)?(?:twitter|x)\.com/[A-Za-z0-9_]{1,50}[^\"'\s<>]*", re.I),
    "facebook":  re.compile(r"https?://(?:www\.)?facebook\.com/[^\"'\s<>]+", re.I),
    "instagram": re.compile(r"https?://(?:www\.)?instagram\.com/[^\"'\s<>]+", re.I),
    "youtube":   re.compile(r"https?://(?:www\.)?youtube\.com/(?:channel|user|c)/[^\"'\s<>]+", re.I),
    "whatsapp":  re.compile(r"https?://(?:wa\.me|api\.whatsapp\.com/send)[^\"'\s<>]+", re.I),
}

_JUNK_FRAGMENTS = {
    "sharer", "share", "intent/tweet", "login", "signup", "policies",
    "legal", "help", "about", "ads", "business", "privacy",
}


def _clean(url: Optional[str]) -> Optional[str]:
    if not url:
        return None
    url = url.rstrip("/.,;")
    lower = url.lower()
    if any(j in lower for j in _JUNK_FRAGMENTS):
        return None
    return url


class SocialExtractor:
    def extract(self, html: str, base_url: str = "") -> SocialLinks:
        soup = BeautifulSoup(html, "lxml")
        hrefs = []
        for a in soup.find_all("a", href=True):
            href = a.get("href", "").strip()
            if href and base_url:
                href = urljoin(base_url, href)
            hrefs.append(href)
        text = " ".join([str(soup), *hrefs])
        links: dict[str, Optional[str]] = {}
        for platform, pattern in _PATTERNS.items():
            m = pattern.search(text)
            links[platform] = _clean(m.group(0)) if m else None
        return SocialLinks(**links)
