"""Tests for the multi-database layer (SQLAlchemy) and APP_LANG switching."""
import pytest
from fastapi.testclient import TestClient

from backend.agent import config
from backend.db import db
from backend.main import app


def _client_no_key(monkeypatch):
    monkeypatch.delenv("LLM_API_KEY", raising=False)
    monkeypatch.setattr(config, "is_llm_configured", lambda: False)
    with TestClient(app) as client:
        yield client


# ---------- DATABASE_URL / SQL_DIALECT ----------

def test_default_url_and_dialect(monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("SQL_DIALECT", raising=False)
    url = db.get_database_url()
    assert url == "sqlite:///./backend/db/trade.db"
    assert db.get_sql_dialect() == "sqlite"
    assert db.describe_db().endswith("trade.db")


def test_dialect_derived_from_url(monkeypatch):
    monkeypatch.delenv("SQL_DIALECT", raising=False)
    monkeypatch.setenv(
        "DATABASE_URL", "postgresql+psycopg2://u:p@localhost:5432/tradequery"
    )
    assert db.get_sql_dialect() == "postgresql"
    assert "postgresql" in db.describe_db()
    assert "p:5432" not in db.describe_db()  # no credentials leaked
    monkeypatch.setenv("DATABASE_URL", "mysql+pymysql://u:p@localhost:3306/tq")
    assert db.get_sql_dialect() == "mysql"
    monkeypatch.setenv("DATABASE_URL", "sqlite:///./backend/db/trade.db")
    assert db.get_sql_dialect() == "sqlite"


def test_explicit_dialect_wins(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "sqlite:///./backend/db/trade.db")
    monkeypatch.setenv("SQL_DIALECT", "mysql")
    assert db.get_sql_dialect() == "mysql"
    monkeypatch.setenv("SQL_DIALECT", "bogus")
    assert db.get_sql_dialect() == "sqlite"  # invalid value falls back to URL


def test_sqlite_path_resolution():
    assert db._sqlite_file_path("sqlite:///./backend/db/trade.db").endswith(
        "backend/db/trade.db"
    )
    assert db._sqlite_file_path("sqlite:////tmp/t.db") == "/tmp/t.db"
    assert db._sqlite_file_path("sqlite:///:memory:") == ":memory:"


def test_init_and_query_temp_sqlite(tmp_path):
    url = f"sqlite:///{tmp_path}/ledger.db"
    db.init_db(url=url)
    expected = {
        "corporates": 8,
        "letters_of_credit": 15,
        "shipments": 20,
        "documents": 28,
        "payments": 8,
    }
    for table, n in expected.items():
        _cols, rows = db.execute_sql(f"SELECT COUNT(*) AS c FROM {table}", url=url)
        assert rows[0]["c"] == n, table
    # Chinese content round-trips byte-identical
    _cols, rows = db.execute_sql(
        "SELECT name_cn FROM corporates WHERE name_en LIKE '%Huamao%'", url=url
    )
    assert rows[0]["name_cn"] == "上海华贸进出口有限公司"
    # idempotent re-init
    db.init_db(url=url)
    _cols, rows = db.execute_sql("SELECT COUNT(*) AS c FROM corporates", url=url)
    assert rows[0]["c"] == 8
    # audit write path works on the new layer
    db.write_audit("测试", "SELECT 1", 1, 1.0, False, None, url=url)
    _cols, rows = db.execute_sql("SELECT question FROM query_audit", url=url)
    assert rows[-1]["question"] == "测试"


def test_read_only_still_enforced(tmp_path):
    url = f"sqlite:///{tmp_path}/ro.db"
    db.init_db(url=url)
    with pytest.raises(Exception):
        db.execute_sql("DROP TABLE corporates", url=url)
    _cols, rows = db.execute_sql("SELECT COUNT(*) AS c FROM corporates", url=url)
    assert rows[0]["c"] == 8


# ---------- APP_LANG ----------

def test_app_lang_parsing(monkeypatch):
    monkeypatch.delenv("APP_LANG", raising=False)
    assert config.app_lang() == "en"
    monkeypatch.setenv("APP_LANG", "zh")
    assert config.app_lang() == "zh"
    monkeypatch.setenv("APP_LANG", "ZH")
    assert config.app_lang() == "zh"
    monkeypatch.setenv("APP_LANG", "fr")
    assert config.app_lang() == "en"


def test_index_served_per_lang(monkeypatch):
    monkeypatch.setenv("APP_LANG", "zh")
    for client in _client_no_key(monkeypatch):
        r = client.get("/")
        assert r.status_code == 200
        assert "贸易金融智能查询" in r.text
        assert 'lang="zh-CN"' in r.text
    monkeypatch.setenv("APP_LANG", "en")
    for client in _client_no_key(monkeypatch):
        r = client.get("/")
        assert r.status_code == 200
        assert "Trade Finance Intelligence" in r.text


def test_chat_messages_follow_lang(monkeypatch):
    monkeypatch.setenv("APP_LANG", "zh")
    for client in _client_no_key(monkeypatch):
        r = client.post("/api/chat", json={"question": "   "})
        assert "请输入你的问题" in r.json()["answer"]
        r = client.post("/api/chat", json={"question": "有哪些逾期未付的款项？"})
        assert r.json()["error"] == "LLM_NOT_CONFIGURED"
        assert "API Key" in r.json()["answer"]


def test_health_reports_lang_and_dialect(monkeypatch):
    monkeypatch.setenv("APP_LANG", "zh")
    for client in _client_no_key(monkeypatch):
        body = client.get("/api/health").json()
        assert body["lang"] == "zh"
        assert body["dialect"] == "sqlite"
