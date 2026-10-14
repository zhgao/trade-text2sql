"""API tests: health / schema / graceful degradation without a key."""
from fastapi.testclient import TestClient

from backend.agent import config
from backend.main import app


def _client_no_key(monkeypatch):
    monkeypatch.delenv("LLM_API_KEY", raising=False)
    monkeypatch.setattr(config, "is_llm_configured", lambda: False)
    with TestClient(app) as client:
        yield client


def test_health(monkeypatch):
    for client in _client_no_key(monkeypatch):
        r = client.get("/api/health")
        assert r.status_code == 200
        body = r.json()
        assert body["status"] == "ok"
        assert body["llm_configured"] is False


def test_schema(monkeypatch):
    for client in _client_no_key(monkeypatch):
        r = client.get("/api/schema")
        assert r.status_code == 200
        tables = r.json()["tables"]
        assert set(tables.keys()) == {
            "corporates",
            "letters_of_credit",
            "shipments",
            "documents",
            "payments",
        }
        assert "L/C number" in tables["letters_of_credit"]["columns"]["lc_number"]


def test_chat_without_key_returns_friendly_message(monkeypatch):
    for client in _client_no_key(monkeypatch):
        r = client.post("/api/chat", json={"question": "Which payments are overdue?"})
        assert r.status_code == 200
        body = r.json()
        assert body["error"] == "LLM_NOT_CONFIGURED"
        assert "no llm api key" in body["answer"].lower()
        assert body["rows"] == []


def test_chat_empty_question(monkeypatch):
    for client in _client_no_key(monkeypatch):
        r = client.post("/api/chat", json={"question": "   "})
        assert r.status_code == 200
        assert "Please enter your question" in r.json()["answer"]


def test_frontend_index_served(monkeypatch):
    for client in _client_no_key(monkeypatch):
        r = client.get("/")
        assert r.status_code == 200
        assert "TradeQuery" in r.text
