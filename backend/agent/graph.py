"""TradeQuery LangGraph Agent: intent -> table selection -> SQL generation -> guardrail -> execute -> repair -> English answer."""
import json
import re
from typing import TypedDict

from langgraph.graph import END, StateGraph

from . import config
from backend.db.db import execute_sql
from .guardrail import GuardrailError, validate_and_fix
from .schema_catalog import SCHEMA_CATALOG, format_few_shots, format_schema

MAX_REPAIR_ATTEMPTS = 2


class AgentState(TypedDict, total=False):
    question: str
    is_chit_chat: bool
    chat_reply: str
    relevant_tables: list
    sql: str
    guardrail_error: str | None
    columns: list
    rows: list
    exec_error: str | None
    attempts: int
    answer: str
    error: str | None


def _ask(prompt: str) -> str:
    llm = config.get_llm()
    if llm is None:
        raise RuntimeError("LLM is not configured")
    resp = llm.invoke(prompt)
    content = getattr(resp, "content", resp)
    return content if isinstance(content, str) else str(content)


def _extract_sql(text: str) -> str:
    # Strip markdown code fences
    m = re.search(r"```(?:sql)?\s*(.*?)```", text, re.S | re.I)
    if m:
        return m.group(1).strip()
    return text.strip()


# ---------------- Nodes ----------------

def n_intent(state: AgentState) -> AgentState:
    q = state["question"]
    prompt = (
        "[INTENT] Intent classification\n"
        "You are TradeQuery, a bank trade-finance assistant. Decide whether the user's "
        "question is a \"business query\" or \"chit-chat\".\n"
        "- Business query: any question about letters of credit, cargo shipment/logistics, "
        "document examination, payments, or corporate clients.\n"
        "- Chit-chat: greetings, thanks, goodbyes, or other conversation unrelated to the business.\n"
        "If it is chit-chat, reply in friendly English and start your reply with \"CHITCHAT:\".\n"
        "If it is a business query, output exactly the two words \"business query\" and nothing else.\n"
        f"\nUser question: {q}"
    )
    out = _ask(prompt).strip()
    if out.startswith("CHITCHAT"):
        reply = out[len("CHITCHAT:"):].strip() or out
        return {"is_chit_chat": True, "chat_reply": reply}
    return {"is_chit_chat": False}


def n_chat_answer(state: AgentState) -> AgentState:
    return {
        "answer": state.get("chat_reply", "Hello!"),
        "sql": "",
        "rows": [],
        "columns": [],
        "error": None,
    }


def n_schema_linking(state: AgentState) -> AgentState:
    q = state["question"]
    prompt = (
        "[SCHEMA] Table selection\n"
        "Below is the schema of the bank trade-finance database "
        "(table and column names are in English, with English descriptions):\n"
        f"\n{format_schema()}\n"
        f"\nUser question: {q}\n"
        "\nSelect the tables needed to answer the question and output only a JSON array, "
        "e.g. [\"letters_of_credit\", \"payments\"], with no other text."
    )
    try:
        out = _ask(prompt).strip()
        m = re.search(r"\[.*?\]", out, re.S)
        tables = json.loads(m.group(0)) if m else []
        tables = [t for t in tables if t in SCHEMA_CATALOG]
    except Exception:  # noqa: BLE001
        tables = []
    if not tables:
        tables = list(SCHEMA_CATALOG.keys())
    return {"relevant_tables": tables}


def n_sql_gen(state: AgentState) -> AgentState:
    q = state["question"]
    tables = state.get("relevant_tables") or None
    prompt = (
        "[SQLGEN] SQL generation\n"
        "You are a SQLite-dialect SQL expert writing queries for a bank trade-finance database.\n"
        "\nRelevant schema:\n"
        f"{format_schema(tables)}\n"
        "\nReference examples:\n"
        f"{format_few_shots()}\n"
        "\nRules:\n"
        "1. Output exactly one SELECT or WITH statement. No explanations, no markdown code fences.\n"
        "2. Table and column names are in English; string literals use the ledger's original values, e.g. status = '\u903e\u671f'.\n"
        "3. \"today\" is date('now'); \"in the next 30 days\" is BETWEEN date('now') AND date('now', '+30 days').\n"
        "4. A company name may appear in either applicant (applicant/importer) or beneficiary "
        "(beneficiary/exporter); when querying a company's business, cover both columns with OR.\n"
        "5. Use COUNT / SUM / GROUP BY for aggregations.\n"
        f"\nUser question: {q}"
    )
    sql = _extract_sql(_ask(prompt))
    return {"sql": sql, "guardrail_error": None, "exec_error": None}


def n_guardrail(state: AgentState) -> AgentState:
    try:
        fixed = validate_and_fix(state.get("sql", ""))
        return {"sql": fixed, "guardrail_error": None}
    except GuardrailError as e:
        return {"guardrail_error": str(e)}


def n_execute(state: AgentState) -> AgentState:
    try:
        columns, rows = execute_sql(state["sql"])
        return {"columns": columns, "rows": rows, "exec_error": None}
    except Exception as e:  # noqa: BLE001
        return {"exec_error": f"{type(e).__name__}: {e}"}


def n_repair(state: AgentState) -> AgentState:
    attempts = state.get("attempts", 0) + 1
    q = state["question"]
    err = state.get("guardrail_error") or state.get("exec_error") or "Query returned 0 rows; the filters may be too strict"
    tables = state.get("relevant_tables") or None
    prompt = (
        "[REPAIR] SQL repair\n"
        "The previously generated SQL failed. Fix it based on the error message and output "
        "one corrected SQLite SELECT query.\n"
        "\nRelevant schema:\n"
        f"{format_schema(tables)}\n"
        f"\nUser question: {q}\n"
        f"Previous SQL: {state.get('sql', '')}\n"
        f"Error: {err}\n"
        "\nHints: guardrail errors are usually misspelled table names; execution errors — "
        "check column names and string quotes; when zero rows are returned, try relaxing "
        "company-name/number matching with LIKE '%keyword%' or drop overly strict date filters.\n"
        "Output only the corrected SQL. No explanations, no markdown code fences."
    )
    sql = _extract_sql(_ask(prompt))
    return {
        "sql": sql,
        "attempts": attempts,
        "guardrail_error": None,
        "exec_error": None,
    }


def n_answer(state: AgentState) -> AgentState:
    q = state["question"]
    rows = state.get("rows", [])
    preview = rows[:20]
    prompt = (
        "[ANSWER] Answer generation\n"
        "You are TradeQuery, a bank trade-finance assistant. Answer the user's question in concise English.\n"
        f"\nUser question: {q}\n"
        f"Executed SQL: {state.get('sql', '')}\n"
        f"Result columns: {state.get('columns', [])}\n"
        f"Query results (total {len(rows)} rows, showing up to 20):\n"
        f"{json.dumps(preview, ensure_ascii=False)}\n"
        "\nRequirements:\n"
        "1. Summarize the answer in English, highlighting key figures, statuses and dates. "
        "Do not invent information that is not in the data.\n"
        "2. Keep original currencies for amounts; quote dates as-is.\n"
        "3. Output only the English answer body, without repeating the SQL."
    )
    answer = _ask(prompt).strip()
    return {"answer": answer, "error": None}


def n_fail(state: AgentState) -> AgentState:
    err = state.get("guardrail_error") or state.get("exec_error") or "unknown error"
    return {
        "answer": f"Sorry, this query could not be completed: {err}. Try rephrasing, e.g. \"Which payments are overdue?\".",
        "error": err,
    }


# ---------------- Routing ----------------

def _route_intent(state: AgentState) -> str:
    return "chat" if state.get("is_chit_chat") else "business"


def _route_guardrail(state: AgentState) -> str:
    if state.get("guardrail_error"):
        if state.get("attempts", 0) < MAX_REPAIR_ATTEMPTS:
            return "repair"
        return "fail"
    return "execute"


def _route_execute(state: AgentState) -> str:
    if state.get("exec_error"):
        if state.get("attempts", 0) < MAX_REPAIR_ATTEMPTS:
            return "repair"
        return "fail"
    rows = state.get("rows") or []
    sql = state.get("sql", "")
    # Suspicious empty result: exact lookup by L/C or B/L number returned 0 rows,
    # the number is probably mistyped — try relaxing it
    suspicious = not rows and re.search(r"(lc_number|bl_number)\s*=", sql, re.I)
    if suspicious and state.get("attempts", 0) < 1:
        return "repair"
    return "answer"


def build_graph():
    g = StateGraph(AgentState)
    g.add_node("intent", n_intent)
    g.add_node("chat_answer", n_chat_answer)
    g.add_node("schema_linking", n_schema_linking)
    g.add_node("sql_gen", n_sql_gen)
    g.add_node("guardrail", n_guardrail)
    g.add_node("execute", n_execute)
    g.add_node("repair", n_repair)
    g.add_node("answer", n_answer)
    g.add_node("fail", n_fail)

    g.set_entry_point("intent")
    g.add_conditional_edges(
        "intent", _route_intent, {"chat": "chat_answer", "business": "schema_linking"}
    )
    g.add_edge("chat_answer", END)
    g.add_edge("schema_linking", "sql_gen")
    g.add_edge("sql_gen", "guardrail")
    g.add_conditional_edges(
        "guardrail", _route_guardrail,
        {"repair": "repair", "execute": "execute", "fail": "fail"},
    )
    g.add_conditional_edges(
        "execute", _route_execute,
        {"repair": "repair", "answer": "answer", "fail": "fail"},
    )
    g.add_edge("repair", "guardrail")
    g.add_edge("answer", END)
    g.add_edge("fail", END)
    return g.compile()


_GRAPH = None


def run_question(question: str) -> dict:
    """Run one full Q&A pass; returns answer/sql/rows/columns/error."""
    global _GRAPH
    if _GRAPH is None:
        _GRAPH = build_graph()
    final = _GRAPH.invoke({"question": question, "attempts": 0})
    return {
        "answer": final.get("answer", ""),
        "sql": final.get("sql", ""),
        "rows": final.get("rows", []),
        "columns": final.get("columns", []),
        "error": final.get("error"),
    }
