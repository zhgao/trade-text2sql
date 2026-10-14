"""Test Fake LLM: routes on prompt markers, returns deterministic results."""
import re
from types import SimpleNamespace


def _question_of(prompt: str) -> str:
    m = re.search(r"User question:\s*(.+)", prompt)
    return m.group(1).strip() if m else ""


BUSINESS_KW = [
    "letter of credit", "l/c", "shipment", "cargo", "bill of lading",
    "b/l", "payment", "overdue", "document", "expir", "company", "corporate",
    "status", "total", "vessel", "port", "invoice", "container", "tenor",
    "scb2026lc", "cosu",
]
GREETING_RE = re.compile(r"\b(hello|hi|thanks|thank you|bye|goodbye)\b", re.I)


def sql_for_question(q: str) -> str:
    """Return deterministic SQL by question keyword (matches the seed data)."""
    ql = q.lower()
    if "total" in ql and ("huamao" in ql or "上海华贸" in q):
        return (
            "SELECT currency, COUNT(*) AS lc_count, SUM(amount) AS total_amount "
            "FROM letters_of_credit "
            "WHERE applicant = '上海华贸进出口有限公司' "
            "OR beneficiary = '上海华贸进出口有限公司' GROUP BY currency"
        )
    if "scb2026lc001207" in ql and "document" in ql:
        return (
            "SELECT doc_type, presented_date, status, discrepancies FROM documents "
            "WHERE lc_number = 'SCB2026LC001207'"
        )
    if "cosu6123456780" in ql:
        return (
            "SELECT bl_number, vessel_voyage, port_loading, port_discharge, status, eta, ata "
            "FROM shipments WHERE bl_number = 'COSU6123456780'"
        )
    if "overdue" in ql:
        return (
            "SELECT lc_number, amount, currency, due_date, payer, payee "
            "FROM payments WHERE status = '逾期'"
        )
    if "expir" in ql:
        return (
            "SELECT lc_number, applicant, beneficiary, amount, currency, expiry_date, status "
            "FROM letters_of_credit "
            "WHERE expiry_date BETWEEN date('now') AND date('now', '+30 days')"
        )
    if "hengxin" in ql or "恒信" in q:
        return (
            "SELECT lc_number, applicant, beneficiary, amount, currency, status, expiry_date "
            "FROM letters_of_credit "
            "WHERE applicant = '深圳恒信电子科技有限公司' "
            "OR beneficiary = '深圳恒信电子科技有限公司'"
        )
    if "scb2026lc001213" in ql:
        return (
            "SELECT bl_number, vessel_voyage, port_loading, port_discharge, status, eta "
            "FROM shipments WHERE lc_number = 'SCB2026LC001213'"
        )
    return "SELECT lc_number, status FROM letters_of_credit"


class FakeChatModel:
    """Simulates ChatOpenAI.invoke(prompt) -> SimpleNamespace(content=str).

    first_bad_sql=True: the first [SQLGEN] call returns illegal SQL,
    to exercise the repair path.
    """

    def __init__(self, first_bad_sql: bool = False):
        self.first_bad_sql = first_bad_sql
        self._sqlgen_calls = 0

    def invoke(self, prompt):
        p = prompt if isinstance(prompt, str) else str(prompt)
        if "[INTENT]" in p:
            q = _question_of(p)
            is_greeting = bool(GREETING_RE.search(q))
            is_business = any(k in q.lower() for k in BUSINESS_KW)
            if is_greeting and not is_business:
                return SimpleNamespace(
                    content="CHITCHAT: Hello! I'm TradeQuery, your trade-finance assistant. "
                    "I can look up letters of credit, cargo, documents and payments — just ask."
                )
            return SimpleNamespace(content="business query")
        if "[SCHEMA]" in p:
            return SimpleNamespace(
                content='["letters_of_credit", "shipments", "documents", "payments", "corporates"]'
            )
        if "[SQLGEN]" in p:
            self._sqlgen_calls += 1
            if self.first_bad_sql and self._sqlgen_calls == 1:
                return SimpleNamespace(content="SELECT * FROM nonexistent_table")
            return SimpleNamespace(content=sql_for_question(_question_of(p)))
        if "[REPAIR]" in p:
            return SimpleNamespace(content=sql_for_question(_question_of(p)))
        if "[ANSWER]" in p:
            m = re.search(r"\(total (\d+) rows", p)
            n = m.group(1) if m else "?"
            return SimpleNamespace(
                content=f"Based on the query results, {n} record(s) were found — see the table below."
            )
        return SimpleNamespace(content="business query")
