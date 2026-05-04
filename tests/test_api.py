"""
API integration tests using httpx TestClient.
"""
import pytest
from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def test_health():
    resp = client.get("/docs")
    assert resp.status_code == 200


def test_generate_missing_prompt():
    resp = client.post("/api/leads/generate", json={"prompt": ""})
    assert resp.status_code == 400


def test_generate_short_prompt():
    resp = client.post("/api/leads/generate", json={"prompt": "hi"})
    assert resp.status_code == 400


def test_generate_returns_job_id():
    resp = client.post("/api/leads/generate", json={"prompt": "Find software companies in Delhi"})
    assert resp.status_code == 202
    data = resp.json()
    assert "job_id" in data
    assert data["status"] == "pending"


def test_job_not_found():
    resp = client.get("/api/jobs/nonexistent-id")
    assert resp.status_code == 404


def test_leads_not_found():
    resp = client.get("/api/leads/nonexistent-id")
    assert resp.status_code == 404


def test_list_jobs_empty():
    resp = client.get("/api/jobs")
    assert resp.status_code == 200
    assert isinstance(resp.json(), list)
