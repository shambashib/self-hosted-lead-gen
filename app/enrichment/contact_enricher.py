"""
Contact Enricher — visits individual listing pages and company websites
to extract real phone numbers and email addresses.

Flow for each lead that has no phone/email:
  1. Fetch the source_url (JustDial/IndiaMART listing page)
  2. Extract phone from tel: links and data-phone attributes
  3. Find the company's own website URL on that page
  4. Crawl the company website for email + additional phone
"""
from __future__ import annotations

import asyncio
import re
from typing import Optional
from urllib.parse import urljoin, urlparse

import structlog
from bs4 import BeautifulSoup

from app.extractors.contact import ContactExtractor
from app.extractors.social import SocialExtractor
from app.models.lead import Lead, SourceType
from app.queue.task_queue import run_tasks_concurrent
from app.scrapers.base import BaseScraper

log = structlog.get_logger(__name__)

_contact_ex = ContactExtractor()
_social_ex = SocialExtractor()

# JustDial / IndiaMART domains — don't follow links back to them
_DIRECTORY_DOMAINS = {"justdial.com", "indiamart.com", "dir.indiamart.com",
                      "yellowpages.in", "sulekha.com", "tradeindia.com"}
_SOCIAL_HOSTS = {
    "linkedin": ("linkedin.com/company/", "linkedin.com/in/"),
    "twitter": ("twitter.com/", "x.com/"),
    "facebook": ("facebook.com/",),
    "instagram": ("instagram.com/",),
    "youtube": ("youtube.com/", "youtu.be/"),
    "whatsapp": ("wa.me/", "whatsapp.com/", "api.whatsapp.com/"),
}
_WEBSITE_SKIP_HOSTS = {
    "facebook.com", "instagram.com", "twitter.com", "x.com", "youtube.com",
    "youtu.be", "linkedin.com", "wa.me", "whatsapp.com", "api.whatsapp.com",
    "google.com", "google.co.in", "maps.google.com",
}
_CONTACT_PATHS = ("contact", "contact-us", "about", "about-us", "reach-us")


def _is_external(href: str) -> bool:
    """True if href points to a non-directory external site."""
    if not href.startswith("http"):
        return False
    host = urlparse(href).netloc.lower().lstrip("www.")
    return not any(d in host for d in _DIRECTORY_DOMAINS)


class ContactEnricher(BaseScraper):

    async def enrich(self, lead: Lead) -> Lead:
        """Attempt to add phone/email/website to a lead that currently lacks them."""
        if (lead.email and lead.phone and not self._missing_socials(lead)) or not lead.source_url:
            return lead

        html = await self._get_listing_html(lead.source_url)
        if not html:
            if not lead.website or self._missing_socials(lead):
                await self._serp_fallback(lead)
            return lead

        soup = BeautifulSoup(html, "lxml")
        self._merge_socials(lead, _social_ex.extract(html, lead.source_url))

        # ── Phone from tel: links / data-phone attrs ───────────────────────
        if not lead.phone:
            lead.phone = self._extract_phone(soup)

        # ── Company website link ───────────────────────────────────────────
        if not lead.website:
            lead.website = self._find_website(soup, lead.source_url)

        if not lead.website or self._missing_socials(lead):
            await self._serp_fallback(lead)

        # ── Crawl company website for email ───────────────────────────────
        if lead.website and (not lead.email or not lead.phone or self._missing_socials(lead)):
            email, phones, socials = await self._crawl_website(lead.website)
            if email:
                lead.email = email
            if phones:
                if not lead.phone:
                    lead.phone = phones[0]
                if len(phones) > 1 and not lead.phone_secondary:
                    lead.phone_secondary = phones[1]
            self._merge_socials(lead, socials)

        # ── Email directly from listing page ──────────────────────────────
        if not lead.email:
            emails = _contact_ex.emails(html)
            if emails:
                lead.email = emails[0]

        return lead

    # ── Helpers ───────────────────────────────────────────────────────────────

    async def _get_listing_html(self, url: str) -> Optional[str]:
        html = await self.fetch(url)
        if html and len(html) > 2000:
            return html
        if __import__("app.config", fromlist=["settings"]).settings.use_playwright:
            from app.scrapers.playwright_helper import playwright_fetch
            return await playwright_fetch(url)
        return html

    @staticmethod
    def _extract_phone(soup: BeautifulSoup) -> Optional[str]:
        # Priority 1: tel: href (most reliable — carries real digits)
        for a in soup.find_all("a", href=True):
            href = a.get("href", "")
            if href.startswith("tel:"):
                digits = re.sub(r"[^\d+]", "", href[4:])
                if len(digits) >= 8:
                    return digits

        # Priority 2: data-phone attribute
        for el in soup.find_all(attrs={"data-phone": True}):
            val = el.get("data-phone", "").strip()
            digits = re.sub(r"[^\d+]", "", val)
            if len(digits) >= 8:
                return digits

        # Priority 3: regex on visible text
        text = soup.get_text()
        phones = re.findall(r"(?<!\d)[6-9]\d{9}(?!\d)", text)
        if phones:
            return "+91" + phones[0]

        return None

    @staticmethod
    def _find_website(soup: BeautifulSoup, source_url: str) -> Optional[str]:
        # Look for explicit website label
        for el in soup.find_all(["a", "span"], string=re.compile(r"website|visit|www\.", re.I)):
            a = el if el.name == "a" else el.find_parent("a")
            if a:
                href = a.get("href", "")
                if _is_external(href):
                    return ContactEnricher._clean_business_url(href)

        # Any external link that looks like a business website
        for a in soup.find_all("a", href=True):
            href = a.get("href", "")
            if _is_external(href):
                cleaned = ContactEnricher._clean_business_url(href)
                if cleaned:
                    return cleaned

        return None

    async def _crawl_website(self, url: str):
        """Fetch homepage + common contact pages; return email, phones, socials."""
        if not url.startswith("http"):
            url = "https://" + url

        pages_html = []
        html = await self.fetch(url)
        if html:
            pages_html.append(html)
            contact_urls = self._find_contact_urls(html, url)
            for path in _CONTACT_PATHS:
                contact_urls.add(url.rstrip("/") + "/" + path)
            for contact_url in list(contact_urls)[:5]:
                ch = await self.fetch(contact_url)
                if ch and len(ch) > 500:
                    pages_html.append(ch)

        if not pages_html:
            return None, [], _social_ex.extract("")

        combined = "\n".join(pages_html)
        emails = _contact_ex.emails(combined)
        phones_raw = _contact_ex.phones(combined)
        socials = _social_ex.extract(combined, url)

        email  = emails[0] if emails else None
        return email, phones_raw, socials

    @staticmethod
    def _find_contact_urls(html: str, base_url: str) -> set[str]:
        soup = BeautifulSoup(html, "lxml")
        base_host = urlparse(base_url).netloc
        urls: set[str] = set()
        for a in soup.find_all("a", href=True):
            href = a.get("href", "")
            label = f"{href} {a.get_text(' ', strip=True)}".lower()
            if not any(slug.replace("-", " ") in label or slug in label for slug in _CONTACT_PATHS):
                continue
            full = urljoin(base_url, href)
            if urlparse(full).netloc == base_host:
                urls.add(full)
        return urls

    async def _serp_fallback(self, lead: Lead) -> None:
        if not lead.business_name:
            return
        try:
            from app.scrapers.serp import make_serp_scraper
            scraper = make_serp_scraper()
            where = lead.city or ""
            query = f'"{lead.business_name}" {where} official website contact email phone social'.strip()
            results = await scraper.search(query, num=8)
            for result in results:
                url = result.url
                if not lead.website:
                    lead.website = self._clean_business_url(url)
                self._set_social_from_url(lead, url)

            if self._missing_socials(lead):
                social_query = (
                    f'"{lead.business_name}" {where} '
                    "site:linkedin.com/company OR site:facebook.com OR site:twitter.com OR site:x.com"
                ).strip()
                social_results = await scraper.search(social_query, num=8)
                for result in social_results:
                    self._set_social_from_url(lead, result.url)
        except Exception as exc:
            log.debug("serp_contact_fallback_failed", lead=lead.business_name, error=str(exc))

    @staticmethod
    def _missing_socials(lead: Lead) -> bool:
        return not all([
            lead.social_links.linkedin,
            lead.social_links.twitter,
            lead.social_links.facebook,
        ])

    @staticmethod
    def _merge_socials(lead: Lead, socials) -> None:
        for field in ("linkedin", "twitter", "facebook", "instagram", "youtube", "whatsapp"):
            if not getattr(lead.social_links, field, None):
                setattr(lead.social_links, field, getattr(socials, field, None))

    @staticmethod
    def _set_social_from_url(lead: Lead, url: str) -> None:
        lower = url.lower()
        for field, hosts in _SOCIAL_HOSTS.items():
            if any(host in lower for host in hosts) and not getattr(lead.social_links, field):
                setattr(lead.social_links, field, url.rstrip("/"))

    @staticmethod
    def _clean_business_url(url: str) -> Optional[str]:
        if not url.startswith("http"):
            return None
        parsed = urlparse(url)
        host = parsed.netloc.lower().lstrip("www.")
        if any(d in host for d in _DIRECTORY_DOMAINS):
            return None
        if any(host == s or host.endswith("." + s) for s in _WEBSITE_SKIP_HOSTS):
            return None
        return url


async def enrich_leads(leads: list[Lead], max_enrich: int = 15) -> list[Lead]:
    """
    Enrich up to `max_enrich` leads that are missing contact info.
    Prioritises leads that are relevant (have business_name + city) but contactless.
    """
    enricher = ContactEnricher()

    # Enrich leads that still need contact details or key public social profiles.
    candidates = [
        l for l in leads
        if l.source_url and (not (l.email and l.phone) or enricher._missing_socials(l))
    ][:max_enrich]

    other = [l for l in leads if l not in candidates]

    if not candidates:
        return leads

    log.info("contact_enrichment_start", candidates=len(candidates))

    enriched = await run_tasks_concurrent(
        [enricher.enrich(lead) for lead in candidates],
        concurrency=5,
    )

    enriched_leads = [l for l in enriched if l is not None]
    gained_email = sum(1 for l in enriched_leads if l.email)
    gained_phone = sum(1 for l in enriched_leads if l.phone)
    log.info("contact_enrichment_done",
             enriched=len(enriched_leads), with_email=gained_email, with_phone=gained_phone)

    return enriched_leads + other
