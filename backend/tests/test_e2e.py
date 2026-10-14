"""End-to-end tests: Fake LLM through the full LangGraph chain,
English question -> SQL -> real data rows."""
import pytest

from backend.agent import config, graph
from backend.tests.fakes import FakeChatModel


@pytest.fixture()
def fake_llm(monkeypatch):
    monkeypatch.setattr(config, "get_llm", lambda: FakeChatModel())
    # the graph module resolves config.get_llm() dynamically; no graph cache reset needed
    yield


def _ask(question: str) -> dict:
    return graph.run_question(question)


def test_doc_status_query(fake_llm):
    r = _ask("What is the document status of L/C SCB2026LC001207?")
    assert r["error"] is None, r
    assert "documents" in r["sql"].lower()
    assert len(r["rows"]) == 4
    bl = [x for x in r["rows"] if x["doc_type"] == "提单"][0]
    assert bl["status"] == "不符点"
    assert "最迟装运期" in (bl["discrepancies"] or "")
    assert "4" in r["answer"]


def test_shipment_tracking_by_bl(fake_llm):
    r = _ask("Where is the cargo under B/L COSU6123456780 right now?")
    assert r["error"] is None, r
    assert "shipments" in r["sql"].lower()
    assert len(r["rows"]) == 1
    row = r["rows"][0]
    assert row["status"] == "在途"
    assert row["port_discharge"] == "上海"
    assert row["vessel_voyage"] == "COSCO UNIVERSE V.045E"


def test_overdue_payments(fake_llm):
    r = _ask("Which payments are overdue?")
    assert r["error"] is None, r
    assert "payments" in r["sql"].lower()
    lcs = {x["lc_number"] for x in r["rows"]}
    assert lcs == {"SCB2026LC001207", "SCB2026LC001213"}
    amounts = {x["amount"] for x in r["rows"]}
    assert amounts == {1150000.0, 275000.0}


def test_corporate_lc_list(fake_llm):
    r = _ask("Which letters of credit does Shenzhen Hengxin (深圳恒信电子科技有限公司) hold?")
    assert r["error"] is None, r
    lcs = {x["lc_number"] for x in r["rows"]}
    # includes L/Cs where it is the applicant and where it is the beneficiary
    assert lcs == {
        "SCB2026LC001203",
        "SCB2026LC001207",
        "SCB2026LC001210",
        "SCB2026LC001214",
    }


def test_expiring_lcs(fake_llm):
    r = _ask("Which letters of credit expire in the next 30 days?")
    assert r["error"] is None, r
    assert "expiry_date" in r["sql"].lower()
    lcs = {x["lc_number"] for x in r["rows"]}
    # baseline date 2026-10-14, 30 days ahead
    assert {"SCB2026LC001201", "SCB2026LC001211", "SCB2026LC001213"} <= lcs
    assert "SCB2026LC001204" not in lcs  # paid and long expired; must not appear


def test_aggregation_total(fake_llm):
    r = _ask("What is the total L/C amount for Shanghai Huamao (上海华贸进出口有限公司)?")
    assert r["error"] is None, r
    assert "sum(" in r["sql"].lower()
    by_ccy = {x["currency"]: x["total_amount"] for x in r["rows"]}
    # 001201 1.25m + 001202 0.86m + 001206 0.78m + 001211 0.66m + 001215 0.505m = 4.055m USD
    assert by_ccy["USD"] == pytest.approx(4055000.0)
    assert r["columns"] == ["currency", "lc_count", "total_amount"]


def test_chit_chat(fake_llm):
    r = _ask("Hello")
    assert r["error"] is None, r
    assert r["sql"] == ""
    assert r["rows"] == []
    assert "Hello" in r["answer"]


def test_repair_after_guardrail_rejection(monkeypatch):
    """First generation uses an illegal table -> guardrail blocks -> repair retries -> success."""
    monkeypatch.setattr(config, "get_llm", lambda: FakeChatModel(first_bad_sql=True))
    r = graph.run_question("Which payments are overdue?")
    assert r["error"] is None, r
    assert "nonexistent_table" not in r["sql"].lower()
    assert {x["lc_number"] for x in r["rows"]} == {
        "SCB2026LC001207",
        "SCB2026LC001213",
    }
