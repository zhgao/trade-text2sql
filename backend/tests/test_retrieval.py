"""Tests for schema pre-retrieval (RAG-for-Text2SQL)."""
import pytest

from backend.agent import config, graph
from backend.agent.retrieval import (
    build_corpus,
    cosine,
    prefilter_tables,
    retrieve_tables,
    tfidf_retrieve,
)
from backend.agent.schema_catalog import SCHEMA_CATALOG


@pytest.fixture(autouse=True)
def clean_retrieval_env(monkeypatch):
    for var in ("SCHEMA_RETRIEVAL", "SCHEMA_TOP_K", "SCHEMA_RETRIEVAL_THRESHOLD",
                "EMBEDDING_MODEL", "LLM_API_KEY"):
        monkeypatch.delenv(var, raising=False)


def test_corpus_covers_all_tables():
    corpus = build_corpus()
    assert {t for t, _ in corpus} == set(SCHEMA_CATALOG.keys())
    assert all(text.strip() for _, text in corpus)


def test_tfidf_ranks_payments_first_for_overdue_query():
    top = tfidf_retrieve("有哪些逾期未付的款项？", top_k=5)
    assert top[0] == "payments"


def test_tfidf_ranks_lc_first_for_expiry_query():
    top = tfidf_retrieve("哪些信用证快到期了？", top_k=5)
    assert top[0] == "letters_of_credit"


def test_tfidf_ranks_shipment_for_bl_query():
    # "提单" is genuinely ambiguous (shipment B/L no. vs. B/L document type);
    # pre-retrieval is a recall stage, so top-2 is the honest assertion.
    top = tfidf_retrieve("提单 COSU6123456780 的货到哪里了？", top_k=5)
    assert "shipments" in top[:2]
    assert "documents" in top[:2]


def test_cosine_zero_vector():
    assert cosine({}, {"a": 1.0}) == 0.0
    assert cosine({"a": 1.0}, {"a": 1.0}) == pytest.approx(1.0)


def test_retrieve_top_k_length_and_unique():
    top = retrieve_tables("付款", top_k=2, method="tfidf")
    assert len(top) == 2
    assert len(set(top)) == 2


def test_empty_query_no_crash():
    top = retrieve_tables("", top_k=3, method="tfidf")
    assert len(top) == 3


def test_prefilter_off_by_default_small_catalog():
    # auto mode + 5 tables <= default threshold 12 -> no prefiltering
    assert prefilter_tables("有哪些逾期未付的款项？") is None


def test_prefilter_explicit_off(monkeypatch):
    monkeypatch.setenv("SCHEMA_RETRIEVAL", "off")
    big = {f"t{i}": {"comment": "x", "columns": {}} for i in range(50)}
    assert prefilter_tables("付款", catalog=big) is None


def test_prefilter_forced_tfidf(monkeypatch):
    monkeypatch.setenv("SCHEMA_RETRIEVAL", "tfidf")
    monkeypatch.setenv("SCHEMA_TOP_K", "2")
    top = prefilter_tables("有哪些逾期未付的款项？")
    assert top is not None and len(top) == 2
    assert top[0] == "payments"


def test_prefilter_auto_triggers_on_many_tables():
    big = {f"t{i}": {"comment": "table", "columns": {"c": "col"}}
           for i in range(20)}
    big["payments"] = SCHEMA_CATALOG["payments"]
    top = prefilter_tables("有哪些逾期未付的款项？", catalog=big)
    assert top is not None and len(top) == 10  # default top_k
    assert "payments" in top


def test_schema_linking_respects_prefilter_scope(monkeypatch):
    seen = {}

    def fake_ask(prompt):
        seen["p"] = prompt
        return '["payments"]'

    monkeypatch.setattr(graph, "prefilter_tables", lambda q: ["payments"])
    monkeypatch.setattr(graph, "_ask", fake_ask)
    out = graph.n_schema_linking({"question": "有哪些逾期未付的款项？"})
    assert out["relevant_tables"] == ["payments"]
    assert "payments" in seen["p"]
    # prompt only saw candidates: other tables' schema blocks must be absent
    # (the instruction example '["letters_of_credit", "payments"]' is fine)
    assert "Table letters_of_credit" not in seen["p"]
    assert "Table payments" in seen["p"]


def test_schema_linking_fallback_to_candidates(monkeypatch):
    monkeypatch.setattr(graph, "prefilter_tables", lambda q: ["payments"])
    monkeypatch.setattr(graph, "_ask", lambda p: "not json at all")
    out = graph.n_schema_linking({"question": "有哪些逾期未付的款项？"})
    assert out["relevant_tables"] == ["payments"]


def test_embedding_falls_back_to_tfidf_without_key():
    # no LLM_API_KEY -> embedding backend unavailable -> tfidf result, no raise
    top = retrieve_tables("有哪些逾期未付的款项？", top_k=3, method="embedding")
    assert top[0] == "payments"


def test_schema_retrieval_settings_defaults():
    s = config.schema_retrieval_settings()
    assert s == {"method": "auto", "top_k": 10, "threshold": 12,
                 "embedding_model": "text-embedding-004"}


def test_schema_retrieval_settings_invalid_method(monkeypatch):
    monkeypatch.setenv("SCHEMA_RETRIEVAL", "banana")
    monkeypatch.setenv("SCHEMA_TOP_K", "not-a-number")
    s = config.schema_retrieval_settings()
    assert s["method"] == "auto"
    assert s["top_k"] == 10
