"""
Contact Info Extractor — regex-based email and phone extraction from raw HTML/text.
"""
from __future__ import annotations

import re
from typing import List

from bs4 import BeautifulSoup

# ─── Patterns ────────────────────────────────────────────────────────────────

_EMAIL_RE = re.compile(
    r"[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,7}",
    re.IGNORECASE,
)

_PHONE_RE = re.compile(
    r"""
    (?:
        (?:\+91[\s\-]?)?            # optional country code
        [6-9]\d{9}                  # Indian mobile
        |
        (?:\+?91[\s\-]?)?
        \(?0\d{2,4}\)?[\s\-]?\d{6,8}  # landline with STD
        |
        \+\d{1,3}[\s\-]?\(?\d{2,4}\)?[\s\-]?\d{6,10}  # international
    )
    """,
    re.VERBOSE,
)

_DISPOSABLE_DOMAINS = {
    "mailinator.com", "guerrillamail.com", "tempmail.com",
    "throwaway.email", "sharklasers.com", "yopmail.com",
}

_INTERNAL_EMAIL_HINTS = {"noreply", "no-reply", "donotreply", "unsubscribe", "support@sentry"}


class ContactExtractor:

    def emails(self, content: str) -> List[str]:
        text = content
        if "<" in content:
            soup = BeautifulSoup(content, "lxml")
            parts = [soup.get_text(" ")]
            for a in soup.find_all("a", href=True):
                href = a.get("href", "")
                if href.lower().startswith("mailto:"):
                    parts.append(href.split(":", 1)[1].split("?", 1)[0])
            text = " ".join(parts)
        raw = _EMAIL_RE.findall(text)
        seen: set = set()
        result: List[str] = []
        for e in raw:
            e = e.lower().strip(".,;")
            domain = e.split("@", 1)[-1]
            if (
                e not in seen
                and domain not in _DISPOSABLE_DOMAINS
                and not any(hint in e for hint in _INTERNAL_EMAIL_HINTS)
                and "." in domain
            ):
                seen.add(e)
                result.append(e)
        return result

    def phones(self, content: str) -> List[str]:
        text = content
        if "<" in content:
            soup = BeautifulSoup(content, "lxml")
            parts = [soup.get_text(" ")]
            for a in soup.find_all("a", href=True):
                href = a.get("href", "")
                if href.lower().startswith(("tel:", "whatsapp://")):
                    parts.append(href)
            for el in soup.find_all(attrs={"data-phone": True}):
                parts.append(el.get("data-phone", ""))
            text = " ".join(parts)
        raw = _PHONE_RE.findall(text)
        seen: set = set()
        result: List[str] = []
        for p in raw:
            cleaned = re.sub(r"[^\d+]", "", p)
            if cleaned not in seen and len(cleaned) >= 8:
                seen.add(cleaned)
                result.append(cleaned)
        return result
