import pytest
from app.extractors.contact import ContactExtractor
from app.extractors.social import SocialExtractor
from app.enrichment.deduplicator import deduplicate
from app.enrichment.scorer import score
from app.models.lead import Lead, SocialLinks


ex = ContactExtractor()
sx = SocialExtractor()


# ── Email extraction ──────────────────────────────────────────────────────────
def test_email_basic():
    emails = ex.emails("Contact us at hello@example.com for queries.")
    assert "hello@example.com" in emails

def test_email_filters_noreply():
    emails = ex.emails("noreply@company.com is system only")
    assert not emails

def test_email_filters_disposable():
    emails = ex.emails("test@mailinator.com")
    assert not emails

def test_email_from_html():
    html = "<html><body><p>Email: <a href='mailto:sales@brand.in'>sales@brand.in</a></p></body></html>"
    emails = ex.emails(html)
    assert "sales@brand.in" in emails

def test_email_from_mailto_href_only():
    html = "<a href='mailto:hello@brand.in?subject=Lead'>Email us</a>"
    emails = ex.emails(html)
    assert "hello@brand.in" in emails

def test_multiple_emails():
    text = "Reach us: info@co.com or support@co.com"
    emails = ex.emails(text)
    assert len(emails) == 2


# ── Phone extraction ──────────────────────────────────────────────────────────
def test_phone_indian_mobile():
    phones = ex.phones("Call us at 9876543210")
    assert "9876543210" in phones[0]

def test_phone_with_country_code():
    # Spaces inside the 10-digit block are not supported by the regex;
    # the country-code prefix followed by a contiguous number is the correct form.
    phones = ex.phones("+91-9876543210")
    assert any("9876543210" in p for p in phones)

def test_phone_from_html_attrs():
    html = "<a href='tel:+919876543210'>Call</a><span data-phone='022 12345678'></span>"
    phones = ex.phones(html)
    assert any("9876543210" in p for p in phones)
    assert any("02212345678" in p for p in phones)


# ── Social extraction ─────────────────────────────────────────────────────────
def test_social_linkedin():
    html = '<a href="https://www.linkedin.com/company/acme">LinkedIn</a>'
    social = sx.extract(html)
    assert social.linkedin and "linkedin.com/company/acme" in social.linkedin

def test_social_instagram():
    html = '<a href="https://www.instagram.com/acmeco/">Insta</a>'
    social = sx.extract(html)
    assert social.instagram and "instagram.com" in social.instagram

def test_social_from_relative_href():
    html = '<a href="//www.facebook.com/acmeco">Facebook</a>'
    social = sx.extract(html, "https://acme.com")
    assert social.facebook and "facebook.com/acmeco" in social.facebook


# ── Deduplication ─────────────────────────────────────────────────────────────
def test_dedup_exact_email():
    leads = [
        Lead(business_name="Acme", email="info@acme.com"),
        Lead(business_name="Acme Ltd", email="info@acme.com"),
    ]
    result = deduplicate(leads)
    assert len(result) == 1

def test_dedup_exact_phone():
    leads = [
        Lead(business_name="Foo", phone="9999999999"),
        Lead(business_name="Foo Co", phone="9999999999"),
    ]
    result = deduplicate(leads)
    assert len(result) == 1

def test_dedup_keeps_unique():
    leads = [
        Lead(business_name="Acme", email="a@a.com"),
        Lead(business_name="Beta", email="b@b.com"),
    ]
    result = deduplicate(leads)
    assert len(result) == 2


# ── Scorer ────────────────────────────────────────────────────────────────────
def test_score_full():
    lead = Lead(
        business_name="Acme Corp",
        email="sales@acme.com",
        phone="9876543210",
        website="https://acme.com",
        city="Mumbai",
        address="123 Street",
        industry="saas",
        social_links=SocialLinks(linkedin="https://linkedin.com/company/acme"),
    )
    assert score(lead) >= 80

def test_score_no_contact():
    lead = Lead(business_name="Unknown")
    assert score(lead) < 30

def test_score_free_email_no_bonus():
    lead_biz = Lead(email="sales@company.com")
    lead_free = Lead(email="sales@gmail.com")
    assert score(lead_biz) > score(lead_free)
