"""Database layer: SQLAlchemy 2.0, driven by DATABASE_URL.

Supported dialects:
- SQLite   (default, demo): DATABASE_URL=sqlite:///./backend/db/trade.db
- PostgreSQL: DATABASE_URL=postgresql+psycopg2://user:pass@host:5432/dbname
- MySQL:      DATABASE_URL=mysql+pymysql://user:pass@host:3306/dbname

Dialect is taken from SQL_DIALECT when set, otherwise derived from the URL.

Security notes:
- SQLite reads go through a `mode=ro` URI connection: any write fails at the
  driver level (defense in depth, on top of the guardrail in agent/guardrail.py).
- For PostgreSQL/MySQL the app itself never writes except the query_audit log;
  the DB account used here MUST be granted SELECT-only on the business tables
  (GRANT SELECT ...) — enforce this at the database layer in production.
"""
import os
import sqlite3
import threading
from datetime import datetime, timezone
from urllib.parse import urlparse

from sqlalchemy import create_engine, text
from sqlalchemy.pool import StaticPool

from .models import TABLES, metadata
from .seed_data import SEED

PROJECT_ROOT = os.path.dirname(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
)

EXEC_TIMEOUT_SEC = 10.0

_ENGINES: dict = {}


def get_database_url() -> str:
    """DATABASE_URL env; default keeps the historical sqlite file behaviour."""
    return os.getenv("DATABASE_URL", "sqlite:///./backend/db/trade.db").strip()


def get_sql_dialect(url: str | None = None) -> str:
    """sqlite | postgresql | mysql. Explicit SQL_DIALECT wins, else derive from URL."""
    explicit = os.getenv("SQL_DIALECT", "").strip().lower()
    if explicit in ("sqlite", "postgresql", "mysql"):
        return explicit
    u = (url or get_database_url()).lower()
    if u.startswith("sqlite"):
        return "sqlite"
    if "postgres" in u:
        return "postgresql"
    if "mysql" in u or "mariadb" in u:
        return "mysql"
    return "sqlite"


def _sqlite_file_path(url: str) -> str:
    """Resolve a sqlite:// URL to a filesystem path (or ':memory:')."""
    rest = url[len("sqlite://") :]
    if rest in (":memory:", "/:memory:"):
        return ":memory:"
    if rest.startswith("//"):
        # sqlite:////abs/path.db -> absolute path
        return os.path.normpath("/" + rest.lstrip("/"))
    # sqlite:///./backend/db/trade.db or sqlite:///trade.db -> relative to project root
    rel = rest[1:] if rest.startswith("/") else rest
    if rel.startswith("./"):
        rel = rel[2:]
    return os.path.normpath(os.path.join(PROJECT_ROOT, rel))


def _make_engine(url: str, read_only: bool = False):
    if url.startswith("sqlite:"):
        path = _sqlite_file_path(url)
        if path == ":memory:":
            # one shared in-memory DB for the whole process (tests)
            return create_engine(
                "sqlite://",
                connect_args={"check_same_thread": False},
                poolclass=StaticPool,
            )
        if read_only:
            # mode=ro: the driver itself refuses any write.
            # check_same_thread=False: pooled connections are used across
            # threads (FastAPI threadpool + our timeout worker thread).
            return create_engine(
                "sqlite://",
                creator=lambda p=path: sqlite3.connect(
                    f"file:{p}?mode=ro", uri=True, check_same_thread=False
                ),
            )
        os.makedirs(os.path.dirname(path), exist_ok=True)
        return create_engine(
            f"sqlite:///{path}", connect_args={"check_same_thread": False}
        )
    return create_engine(url, pool_pre_ping=True)


def _engine(url: str, read_only: bool = False):
    if ":memory:" in url:
        key = ("sqlite-memory",)
    else:
        key = (url, read_only)
    if key not in _ENGINES:
        _ENGINES[key] = _make_engine(url, read_only)
    return _ENGINES[key]


def init_db(url: str | None = None, force: bool = False) -> str:
    """Create tables (if needed) and load seed data when the ledger is empty.

    Idempotent: safe to call on every startup, like the old version.
    """
    durl = url or get_database_url()
    engine = _engine(durl, read_only=False)
    metadata.create_all(engine)
    with engine.begin() as conn:
        existing = conn.execute(text("SELECT COUNT(*) FROM corporates")).scalar()
        if force or not existing:
            for table_name, rows in SEED.items():
                if rows:
                    conn.execute(TABLES[table_name].insert(), rows)
    return durl


def execute_sql(sql: str, timeout: float = EXEC_TIMEOUT_SEC, url: str | None = None):
    """Read-only execution of one SQL statement.

    Returns (columns, rows[dict]). Raises on timeout or DB error.
    The 10-second thread guard applies to every dialect.
    """
    durl = url or get_database_url()
    box: dict = {}

    def _run():
        try:
            engine = _engine(durl, read_only=True)
            with engine.connect() as conn:
                result = conn.execute(text(sql))
                cols = list(result.keys())
                rows = [dict(r) for r in result.mappings().all()]
            box["ok"] = (cols, rows)
        except Exception as e:  # noqa: BLE001
            box["err"] = e

    t = threading.Thread(target=_run, daemon=True)
    t.start()
    t.join(timeout)
    if t.is_alive():
        raise TimeoutError(f"SQL execution timed out (>{timeout:.0f}s); wait aborted")
    if "err" in box:
        raise box["err"]
    return box["ok"]


def write_audit(
    question: str,
    sql_text: str,
    row_count: int,
    elapsed_ms: float,
    has_error: bool,
    error_msg: str | None,
    url: str | None = None,
) -> None:
    """Append one row to query_audit. Never raises (prints a warning instead)."""
    try:
        durl = url or get_database_url()
        engine = _engine(durl, read_only=False)
        with engine.begin() as conn:
            conn.execute(
                TABLES["query_audit"]
                .insert()
                .values(
                    ts=datetime.now(timezone.utc).isoformat(),
                    question=question,
                    sql_text=sql_text,
                    row_count=row_count,
                    elapsed_ms=elapsed_ms,
                    has_error=1 if has_error else 0,
                    error_msg=error_msg,
                )
            )
    except Exception as e:  # noqa: BLE001
        print(f"[audit] Failed to write audit log: {e}")


def describe_db(url: str | None = None) -> str:
    """Short human-readable DB description for /api/health (no credentials)."""
    durl = url or get_database_url()
    dialect = get_sql_dialect(durl)
    if dialect == "sqlite":
        path = _sqlite_file_path(durl)
        return os.path.basename(path) if path != ":memory:" else ":memory:"
    parsed = urlparse(durl)
    host = parsed.hostname or "?"
    name = (parsed.path or "").lstrip("/") or "?"
    return f"{dialect}@{host}/{name}"
