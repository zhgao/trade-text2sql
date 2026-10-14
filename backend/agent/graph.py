"""TradeQuery LangGraph Agent: intent -> table selection -> SQL generation -> guardrail -> execute -> repair -> answer.

Bilingual (APP_LANG=en/zh) and multi-dialect (sqlite/postgresql/mysql):
prompts are built per language, and the SQL-generation rules carry a
dialect date-expression cheat sheet.
"""
import json
import re
from typing import TypedDict

from langgraph.graph import END, StateGraph

from . import config
from backend.db.db import execute_sql, get_sql_dialect
from .guardrail import GuardrailError, validate_and_fix
from .retrieval import prefilter_tables
from .schema_catalog import (
    DIALECT_DATE,
    SCHEMA_CATALOG,
    format_few_shots,
    format_schema,
)

MAX_REPAIR_ATTEMPTS = 2


def _lang() -> str:
    """'en' or 'zh', from APP_LANG."""
    return config.app_lang()


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
    if _lang() == "zh":
        prompt = (
            "[INTENT] 意图判断\n"
            "你是银行贸易金融助手 TradeQuery。请判断用户问题属于\"业务查询\"还是\"闲聊\"。\n"
            "- 业务查询：关于信用证、货物发运/物流、单据审单、付款、企业客户的任何问题。\n"
            "- 闲聊：打招呼、感谢、告别等与业务无关的聊天。\n"
            "如果是闲聊，直接用中文友好回复，回复以\"闲聊：\"开头。\n"
            "如果是业务查询，只输出\"业务查询\"四个字，不要输出其他内容。\n"
            f"\n用户问题：{q}"
        )
    else:
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
    # Accept either marker regardless of language (robust against model drift)
    if out.startswith("CHITCHAT"):
        reply = out[len("CHITCHAT:"):].strip() or out
        return {"is_chit_chat": True, "chat_reply": reply}
    if out.startswith("闲聊"):
        reply = out.split("：", 1)[1].strip() if "：" in out else out
        return {"is_chit_chat": True, "chat_reply": reply or out}
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
    # 表很多时先做检索预筛（RAG-for-Text2SQL）；表少时返回 None，走全量 schema。
    candidates = prefilter_tables(q)
    scope = candidates or list(SCHEMA_CATALOG.keys())
    if _lang() == "zh":
        prompt = (
            "[SCHEMA] 表选择\n"
            "下面是银行贸易金融数据库的表结构（表名列名为英文，附英文说明）：\n"
            f"\n{format_schema(scope)}\n"
            f"\n用户问题：{q}\n"
            "\n选出回答该问题需要的表，只输出 JSON 数组，如 [\"letters_of_credit\", \"payments\"]，不要输出其他文字。"
        )
    else:
        prompt = (
            "[SCHEMA] Table selection\n"
            "Below is the schema of the bank trade-finance database "
            "(table and column names are in English, with English descriptions):\n"
            f"\n{format_schema(scope)}\n"
            f"\nUser question: {q}\n"
            "\nSelect the tables needed to answer the question and output only a JSON array, "
            "e.g. [\"letters_of_credit\", \"payments\"], with no other text."
        )
    try:
        out = _ask(prompt).strip()
        m = re.search(r"\[.*?\]", out, re.S)
        tables = json.loads(m.group(0)) if m else []
        tables = [t for t in tables if t in scope]
    except Exception:  # noqa: BLE001
        tables = []
    if not tables:
        tables = scope
    return {"relevant_tables": tables}


def n_sql_gen(state: AgentState) -> AgentState:
    q = state["question"]
    tables = state.get("relevant_tables") or None
    lang = _lang()
    dialect = get_sql_dialect()
    date_expr = DIALECT_DATE.get(dialect, DIALECT_DATE["sqlite"])
    few_shots = format_few_shots(lang=lang, dialect=dialect)
    if lang == "zh":
        prompt = (
            "[SQLGEN] SQL \u751f\u6210\n"
            f"\u4f60\u662f\u4e00\u4f4d\u7cbe\u901a{dialect}\u65b9\u8a00\u7684 SQL \u4e13\u5bb6\uff0c\u4e3a\u94f6\u884c\u8d38\u6613\u91d1\u878d\u6570\u636e\u5e93\u5199\u67e5\u8be2\u3002\n"
            "\n\u76f8\u5173\u8868\u7ed3\u6784\uff1a\n"
            f"{format_schema(tables)}\n"
            "\n\u53c2\u8003\u793a\u4f8b\uff1a\n"
            f"{few_shots}\n"
            "\n\u89c4\u5219\uff1a\n"
            "1. \u53ea\u8f93\u51fa\u4e00\u6761 SELECT \u6216 WITH \u8bed\u53e5\u3002\u4e0d\u8981\u89e3\u91ca\uff0c\u4e0d\u8981 markdown \u4ee3\u7801\u56f4\u680f\u3002\n"
            "2. \u8868\u540d\u5217\u540d\u4e3a\u82f1\u6587\uff1b\u5b57\u7b26\u4e32\u5b57\u9762\u91cf\u7528\u8d26\u672c\u539f\u6587\uff0c\u4f8b\u5982 status = '\u903e\u671f'\u3002\n"
            f"3. \u201c\u4eca\u5929\u201d\u662f {date_expr['today']}\uff1b\u201c\u672a\u676530\u5929\u201d\u662f BETWEEN {date_expr['today']} AND {date_expr['next30']}\u3002\n"
            "4. \u516c\u53f8\u540d\u53ef\u80fd\u51fa\u73b0\u5728 applicant\uff08\u5f00\u8bc1\u7533\u8bf7\u4eba/\u8fdb\u53e3\u5546\uff09\u6216 beneficiary\uff08\u53d7\u76ca\u4eba/\u51fa\u53e3\u5546\uff09"
            "\u4efb\u4e00\u5217\uff1b\u67e5\u67d0\u516c\u53f8\u7684\u4e1a\u52a1\u65f6\uff0c\u4e24\u5217\u90fd\u8981\u7528 OR \u8986\u76d6\u3002\n"
            "5. \u805a\u5408\u7528 COUNT / SUM / GROUP BY\u3002\n"
            f"\n\u7528\u6237\u95ee\u9898\uff1a{q}"
        )
    else:
        prompt = (
            "[SQLGEN] SQL generation\n"
            f"You are a {dialect}-dialect SQL expert writing queries for a bank trade-finance database.\n"
            "\nRelevant schema:\n"
            f"{format_schema(tables)}\n"
            "\nReference examples:\n"
            f"{few_shots}\n"
            "\nRules:\n"
            "1. Output exactly one SELECT or WITH statement. No explanations, no markdown code fences.\n"
            "2. Table and column names are in English; string literals use the ledger's original values, e.g. status = '\u903e\u671f'.\n"
            f"3. \"today\" is {date_expr['today']}; \"in the next 30 days\" is BETWEEN {date_expr['today']} AND {date_expr['next30']}.\n"
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
    err = state.get("guardrail_error") or state.get("exec_error") or (
        "\u67e5\u8be2\u8fd4\u56de 0 \u884c\uff0c\u8fc7\u6ee4\u6761\u4ef6\u53ef\u80fd\u592a\u4e25\u683c"
        if _lang() == "zh"
        else "Query returned 0 rows; the filters may be too strict"
    )
    tables = state.get("relevant_tables") or None
    dialect = get_sql_dialect()
    if _lang() == "zh":
        prompt = (
            "[REPAIR] SQL \u4fee\u590d\n"
            "\u4e4b\u524d\u751f\u6210\u7684 SQL \u6267\u884c\u5931\u8d25\u3002\u8bf7\u6839\u636e\u9519\u8bef\u4fe1\u606f\u4fee\u590d\uff0c"
            f"\u8f93\u51fa\u4e00\u6761\u4fee\u6b63\u540e\u7684{dialect}\u65b9\u8a00 SELECT \u67e5\u8be2\u3002\n"
            "\n\u76f8\u5173\u8868\u7ed3\u6784\uff1a\n"
            f"{format_schema(tables)}\n"
            f"\n\u7528\u6237\u95ee\u9898\uff1a{q}\n"
            f"\u4e4a\u524d\u7684 SQL\uff1a{state.get('sql', '')}\n"
            f"\u9519\u8bef\uff1a{err}\n"
            "\n\u63d0\u793a\uff1a\u62a4\u680f\u62a6\u622a\u591a\u534a\u662f\u8868\u540d\u5199\u9519\uff1b"
            "\u6267\u884c\u9519\u8bef\u8bf7\u68c0\u67e5\u5217\u540d\u548c\u5f15\u53f7\uff1b"
            "\u8fd4\u56de 0 \u884c\u65f6\uff0c\u53ef\u4ee5\u7528 LIKE '%\u5173\u952e\u8bcd%' \u653e\u5bbd\u516c\u53f8\u540d/"
            "\u7f16\u53f7\u5339\u914d\uff0c\u6216\u53bb\u6389\u8fc7\u4e8e\u4e25\u683c\u7684\u65e5\u671f\u6761\u4ef6\u3002\n"
            "\u53ea\u8f93\u51fa\u4fee\u6b63\u540e\u7684 SQL\u3002\u4e0d\u8981\u89e3\u91ca\uff0c\u4e0d\u8981 markdown \u4ee3\u7801\u56f4\u680f\u3002"
        )
    else:
        prompt = (
            "[REPAIR] SQL repair\n"
            "The previously generated SQL failed. Fix it based on the error message and output "
            f"one corrected {dialect} SELECT query.\n"
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
    if _lang() == "zh":
        prompt = (
            "[ANSWER] \u751f\u6210\u56de\u7b54\n"
            "\u4f60\u662f\u94f6\u884c\u8d38\u6613\u91d1\u878d\u52a9\u624b TradeQuery\u3002\u8bf7\u7528\u7b80\u6d01\u4e2d\u6587\u56de\u7b54\u7528\u6237\u7684\u95ee\u9898\u3002\n"
            f"\n\u7528\u6237\u95ee\u9898\uff1a{q}\n"
            f"\u6267\u884c\u7684 SQL\uff1a{state.get('sql', '')}\n"
            f"\u7ed3\u679c\u5217\uff1a{state.get('columns', [])}\n"
            f"\u67e5\u8be2\u7ed3\u679c\uff08\u5171 {len(rows)} \u884c\uff0c\u6700\u591a\u5c55\u793a 20 \u884c\uff09\uff1a\n"
            f"{json.dumps(preview, ensure_ascii=False)}\n"
            "\n\u8981\u6c42\uff1a\n"
            "1. \u7528\u4e2d\u6587\u603b\u7ed3\u7b54\u6848\uff0c\u7a81\u51fa\u5173\u952e\u6570\u5b57\u3001\u72b6\u6001\u548c\u65e5\u671f\u3002"
            "\u4e0d\u8981\u7f16\u9020\u6570\u636e\u4e2d\u6ca1\u6709\u7684\u4fe1\u606f\u3002\n"
            "2. \u91d1\u989d\u4fdd\u6301\u539f\u5e01\u79cd\uff1b\u65e5\u671f\u6309\u539f\u6837\u5f15\u7528\u3002\n"
            "3. \u53ea\u8f93\u51fa\u4e2d\u6587\u56de\u7b54\u6b63\u6587\uff0c\u4e0d\u8981\u91cd\u590d SQL\u3002"
        )
    else:
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
    if _lang() == "zh":
        answer = (
            f"\u62b1\u6b49\uff0c\u8fd9\u4e2a\u67e5\u8be2\u6ca1\u80fd\u5b8c\u6210\uff1a{err}\u3002"
            "\u8bf7\u6362\u4e2a\u95ee\u6cd5\u8bd5\u8bd5\uff0c\u4f8b\u5982\u201c\u6709\u54ea\u4e9b\u903e\u671f\u672a\u4ed8\u7684\u6b3e\u9879\uff1f\u201d\u3002"
        )
    else:
        answer = (
            f"Sorry, this query could not be completed: {err}. "
            'Try rephrasing, e.g. "Which payments are overdue?".'
        )
    return {"answer": answer, "error": err}


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
