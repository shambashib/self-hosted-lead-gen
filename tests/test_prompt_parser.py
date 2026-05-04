import pytest
from app.core.prompt_parser import _rule_based_parse


def test_skincare_mumbai():
    p = _rule_based_parse("Find D2C skincare brands in Mumbai")
    assert p.industry == "skincare"
    assert p.location == "Mumbai"
    assert p.intent == "B2B"


def test_real_estate_kolkata():
    p = _rule_based_parse("Find real estate agents in Kolkata")
    assert p.industry == "real_estate"
    assert p.location == "Kolkata"


def test_saas_india():
    p = _rule_based_parse("Find SaaS founders in India")
    assert p.industry == "saas"


def test_no_location():
    p = _rule_based_parse("Find pharma companies")
    assert p.industry == "healthcare"
    assert p.location is None


def test_b2c_intent():
    p = _rule_based_parse("Find individual buyers of cosmetics")
    assert p.intent == "B2C"


def test_keywords_extracted():
    p = _rule_based_parse("Find IT consulting companies in Bangalore")
    assert len(p.keywords) > 0
