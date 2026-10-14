"""TradeQuery backend: FastAPI + static frontend + audit log.

APP_LANG=en serves frontend/index.html, APP_LANG=zh serves frontend/index.zh.html.
"""
import os
import time

from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from backend.agent import config
from backend.agent.graph import run_question
from backend.agent.guardrail import ALLOWED_TABLES
from backend.agent.schema_catalog import SCHEMA_CATALOG
from backend.db.db import describe_db, execute_sql, get_sql_dialect, init_db, write_audit

load_dotenv()

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
FRONTEND_DIR = os.path.join(os.path.dirname(BASE_DIR), "frontend")

app = FastAPI(title="TradeQuery – Trade Finance Intelligence", version="1.0.0")


class ChatRequest(BaseModel):
    question: str


class ChatResponse(BaseModel):
    answer: str
    sql: str = ""
    rows: list = []
    columns: list = []
    error: str | None = None


def _t(key: str) -> str:
    """Tiny UI/message i18n for the API layer (follows APP_LANG)."""
    zh = config.app_lang() == "zh"
    texts = {
        "empty_question": (
            "请输入你的问题，例如“有哪些逾期未付的款项？”。"
            if zh
            else 'Please enter your question, e.g. "Which payments are overdue?".'
        ),
        "no_key": (
            "抱歉，尚未配置 LLM API Key，无法理解你的问题。\n"
            "请在项目根目录创建 .env 文件并填入 LLM_API_KEY（可参考 .env.example），"
            "然后重启服务。数据库与查询链路本身是正常的。"
            if zh
            else "Sorry, no LLM API key is configured, so I can't understand your question.\n"
            "Create a .env file in the project root with LLM_API_KEY (see .env.example) "
            "and restart the service. The database and query pipeline themselves are fine."
        ),
        "query_error": "抱歉，查询出错：" if zh else "Sorry, an error occurred during the query: ",
    }
    return texts[key]


def _write_audit(question, sql_text, row_count, elapsed_ms, has_error, error_msg):
    """Write the audit log; a failure never breaks the main flow."""
    try:
        write_audit(question, sql_text, row_count, elapsed_ms, has_error, error_msg)
    except Exception as e:  # noqa: BLE001
        print(f"[audit] Failed to write audit log: {e}")


@app.on_event("startup")
def _startup():
    init_db()
    print(f"[TradeQuery] Database ready: {describe_db()}")
    print(f"[TradeQuery] LLM configured: {config.is_llm_configured()}")
    print(f"[TradeQuery] Language: {config.app_lang()}")


@app.get("/api/health")
def health():
    return {
        "status": "ok",
        "service": "TradeQuery",
        "llm_configured": config.is_llm_configured(),
        "llm_model": config.llm_settings()["model"],
        "db": describe_db(),
        "lang": config.app_lang(),
        "dialect": get_sql_dialect(),
    }


@app.get("/api/schema")
def schema():
    """Return the table schema with English annotations (for frontend/debugging)."""
    return {"tables": SCHEMA_CATALOG}


TABLE_LIMIT_DEFAULT = 50
TABLE_LIMIT_MAX = 200


def _clamp_limit(limit, default=TABLE_LIMIT_DEFAULT, maximum=TABLE_LIMIT_MAX):
    """Clamp a row limit to [1, maximum]; fall back to default when unparseable."""
    try:
        n = int(limit)
    except (TypeError, ValueError):
        return default
    return max(1, min(n, maximum))


@app.get("/api/tables/{table_name}")
def table_rows(table_name: str, limit: int = TABLE_LIMIT_DEFAULT):
    """Browse raw rows of a whitelisted business table (read-only)."""
    if table_name not in ALLOWED_TABLES:
        return JSONResponse(
            status_code=403,
            content={"detail": f"Table '{table_name}' is not available for browsing"},
        )
    n = _clamp_limit(limit)
    # table_name comes from the whitelist above, so interpolation is safe
    columns, rows = execute_sql(f"SELECT * FROM {table_name} LIMIT {n}")
    return {
        "table": table_name,
        "columns": columns,
        "rows": rows,
        "limit": n,
        "count": len(rows),
    }


@app.get("/api/audit")
def audit(limit: int = TABLE_LIMIT_DEFAULT):
    """Return the most recent query-audit records, newest first (read-only)."""
    n = _clamp_limit(limit)
    _columns, rows = execute_sql(
        "SELECT id, ts, question, sql_text, row_count, elapsed_ms, has_error, error_msg "
        f"FROM query_audit ORDER BY ts DESC LIMIT {n}"
    )
    return rows


@app.post("/api/chat", response_model=ChatResponse)
def chat(req: ChatRequest):
    question = (req.question or "").strip()
    if not question:
        return ChatResponse(answer=_t("empty_question"))

    started = time.perf_counter()
    if not config.is_llm_configured():
        _write_audit(question, "", 0, 0.0, True, "LLM_NOT_CONFIGURED")
        return ChatResponse(answer=_t("no_key"), error="LLM_NOT_CONFIGURED")

    try:
        result = run_question(question)
    except Exception as e:  # noqa: BLE001
        elapsed_ms = (time.perf_counter() - started) * 1000
        _write_audit(question, "", 0, elapsed_ms, True, f"{type(e).__name__}: {e}")
        return ChatResponse(
            answer=f"{_t('query_error')}{e}",
            error=f"{type(e).__name__}: {e}",
        )

    elapsed_ms = (time.perf_counter() - started) * 1000
    has_error = bool(result.get("error"))
    _write_audit(
        question,
        result.get("sql", ""),
        len(result.get("rows", [])),
        elapsed_ms,
        has_error,
        result.get("error"),
    )
    return ChatResponse(
        answer=result.get("answer", ""),
        sql=result.get("sql", ""),
        rows=result.get("rows", []),
        columns=result.get("columns", []),
        error=result.get("error"),
    )


@app.get("/")
def index():
    """Serve the landing page in the configured language (APP_LANG)."""
    fname = "index.zh.html" if config.app_lang() == "zh" else "index.html"
    return FileResponse(os.path.join(FRONTEND_DIR, fname))


# Static assets (favicon, etc.). Mounted WITHOUT html=True so the explicit
# "/" route above (language-aware) always wins.
if os.path.isdir(FRONTEND_DIR):
    app.mount("/", StaticFiles(directory=FRONTEND_DIR, html=False), name="frontend")


@app.exception_handler(404)
async def _not_found(request, exc):
    if request.url.path.startswith("/api/"):
        return JSONResponse(status_code=404, content={"detail": "Endpoint not found"})
    return JSONResponse(status_code=404, content={"detail": "Page not found"})


if __name__ == "__main__":
    import uvicorn

    port = int(os.getenv("PORT", "8000"))
    uvicorn.run("backend.main:app", host="0.0.0.0", port=port, reload=False)
