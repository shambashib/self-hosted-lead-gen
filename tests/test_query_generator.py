import pytest
from app.core.query_generator import generate_queries
from app.models.job import ParsedPrompt


def test_generates_queries():
    p = ParsedPrompt(raw="test", industry="skincare", location="Mumbai", intent="B2B")
    queries = generate_queries(p)
    assert len(queries) >= 3
    assert any("skincare" in q.lower() for q in queries)


def test_directory_queries_included():
    p = ParsedPrompt(raw="test", industry="real_estate", location="Kolkata", intent="B2B")
    queries = generate_queries(p)
    assert any("indiamart" in q.lower() or "justdial" in q.lower() for q in queries)


def test_no_duplicates():
    p = ParsedPrompt(raw="test", industry="saas", location="Bangalore", intent="B2B")
    queries = generate_queries(p)
    assert len(queries) == len(set(queries))


def test_max_ten():
    p = ParsedPrompt(raw="test", industry="pharma", location="Delhi", intent="B2B")
    assert len(generate_queries(p)) <= 10
