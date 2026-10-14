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


def test_tables_rejects_non_whitelisted(monkeypatch):
    for client in _client_no_key(monkeypatch):
        r = client.get("/api/tables/hackers")
        assert r.status_code == 403
        r = client.get("/api/tables/sqlite_master")
        assert r.status_code == 403


def test_tables_returns_rows(monkeypatch):
    for client in _client_no_key(monkeypatch):
        r = client.get("/api/tables/letters_of_credit")
        assert r.status_code == 200
        body = r.json()
        assert body["table"] == "letters_of_credit"
        assert "lc_number" in body["columns"]
        assert body["count"] == len(body["rows"]) == 15
        assert body["rows"][0]["lc_number"].startswith("SCB2026LC")


def test_tables_limit_clamped(monkeypatch):
    from backend.main import _clamp_limit

    assert _clamp_limit(999) == 200
    assert _clamp_limit(0) == 1
    assert _clamp_limit(None) == 50
    for client in _client_no_key(monkeypatch):
        r = client.get("/api/tables/shipments?limit=999")
        assert r.status_code == 200
        assert r.json()["limit"] == 200


def test_audit_returns_array(monkeypatch):
    for client in _client_no_key(monkeypatch):
        client.post("/api/chat", json={"question": "Which payments are overdue?"})
        r = client.get("/api/audit")
        assert r.status_code == 200
        records = r.json()
        assert isinstance(records, list)
        assert len(records) >= 1
        latest = records[0]
        assert latest["question"] == "Which payments are overdue?"
        assert "ts" in latest and "sql_text" in latest
