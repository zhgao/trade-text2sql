"""SQLite 访问层：建库/种子、只读执行（10 秒超时保护）。"""
import os
import sqlite3
import threading

BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB_PATH = os.path.join(BACKEND_DIR, "db", "trade.db")
SCHEMA_SQL = os.path.join(BACKEND_DIR, "db", "schema.sql")
SEED_SQL = os.path.join(BACKEND_DIR, "db", "seed.sql")

EXEC_TIMEOUT_SEC = 10.0


def init_db(force: bool = False) -> str:
    """初始化数据库：建表 + 灌种子数据。已存在且有数据时跳过（除非 force）。"""
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    need_seed = force or not os.path.exists(DB_PATH)
    conn = sqlite3.connect(DB_PATH, timeout=10.0)
    try:
        with open(SCHEMA_SQL, "r", encoding="utf-8") as f:
            conn.executescript(f.read())
        if not need_seed:
            cur = conn.execute("SELECT COUNT(*) FROM corporates")
            need_seed = cur.fetchone()[0] == 0
        if need_seed:
            with open(SEED_SQL, "r", encoding="utf-8") as f:
                conn.executescript(f.read())
        conn.commit()
    finally:
        conn.close()
    return DB_PATH


def _read_only_conn() -> sqlite3.Connection:
    # mode=ro：只读打开，任何写入都会直接失败（纵深防御）
    conn = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True, timeout=10.0)
    conn.row_factory = sqlite3.Row
    return conn


def execute_sql(sql: str, timeout: float = EXEC_TIMEOUT_SEC):
    """只读执行 SQL，返回 (columns, rows[dict])；超时或报错时抛异常。"""
    box: dict = {}

    def _run():
        conn = None
        try:
            conn = _read_only_conn()
            cur = conn.execute(sql)
            cols = [d[0] for d in cur.description] if cur.description else []
            rows = [dict(r) for r in cur.fetchall()]
            box["ok"] = (cols, rows)
        except Exception as e:  # noqa: BLE001
            box["err"] = e
        finally:
            if conn is not None:
                try:
                    conn.close()
                except Exception:  # noqa: BLE001
                    pass

    t = threading.Thread(target=_run, daemon=True)
    t.start()
    t.join(timeout)
    if t.is_alive():
        raise TimeoutError(f"SQL execution timed out (>{timeout:.0f}s); wait aborted")
    if "err" in box:
        raise box["err"]
    return box["ok"]
