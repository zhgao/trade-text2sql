"""SQL 安全护栏：只允许单条只读 SELECT/WITH 查询。"""
import re

import sqlparse

# 允许查询的表白名单（5 张业务表）
ALLOWED_TABLES = {
    "corporates",
    "letters_of_credit",
    "shipments",
    "documents",
    "payments",
}

# 禁止出现的关键字（去字符串字面量后做词边界匹配）
BANNED_KEYWORDS = {
    "insert", "update", "delete", "drop", "create", "alter", "truncate",
    "grant", "revoke", "replace", "attach", "detach", "vacuum", "pragma",
    "reindex", "savepoint", "begin", "commit", "rollback",
}

DEFAULT_LIMIT = 50
MAX_LIMIT = 200


class GuardrailError(ValueError):
    """Guardrail rejection; message is an English explanation."""


def _strip_string_literals(sql: str) -> str:
    # 去掉单引号字符串，避免 'DROP' 这种出现在值里的词被误杀
    return re.sub(r"'(?:[^']|'')*'", "''", sql)


def _extract_tables(sql_no_literals: str) -> "set[str]":
    tables = set()
    for m in re.finditer(r"\bfrom\s+([a-zA-Z_][a-zA-Z0-9_]*)", sql_no_literals, re.I):
        tables.add(m.group(1).lower())
    for m in re.finditer(r"\bjoin\s+([a-zA-Z_][a-zA-Z0-9_]*)", sql_no_literals, re.I):
        tables.add(m.group(1).lower())
    return tables


def validate_and_fix(sql: str) -> str:
    """校验并修正 SQL。返回可执行的安全 SQL；不合规则时抛 GuardrailError。"""
    cleaned = sql.strip()
    # 去掉末尾分号后，中间不允许再出现分号（防多语句堆叠）
    cleaned = cleaned.rstrip(";").strip()
    if not cleaned:
        raise GuardrailError("SQL is empty; execution refused")
    if ";" in cleaned:
        raise GuardrailError("Only a single SQL statement is allowed; multiple statements detected")

    # 用 sqlparse 做语句切分二次确认
    statements = [s for s in sqlparse.parse(cleaned) if str(s).strip()]
    if len(statements) != 1:
        raise GuardrailError("Only a single SQL statement is allowed")

    # Must start with SELECT / WITH
    first_word = re.match(r"\s*([a-zA-Z]+)", cleaned)
    if not first_word or first_word.group(1).lower() not in ("select", "with"):
        raise GuardrailError("Only SELECT / WITH queries are allowed; DDL/DML is forbidden")

    no_literals = _strip_string_literals(cleaned)
    lowered = no_literals.lower()

    # Banned keywords
    for kw in BANNED_KEYWORDS:
        if re.search(rf"\b{kw}\b", lowered):
            raise GuardrailError(f"Banned keyword detected: {kw.upper()}; execution refused")

    # Table whitelist
    tables = _extract_tables(no_literals)
    if not tables:
        raise GuardrailError("SQL does not reference any permitted table")
    illegal = tables - ALLOWED_TABLES
    if illegal:
        raise GuardrailError(f"Reference to unauthorized table(s): {', '.join(sorted(illegal))}")

    # LIMIT：没有则补默认 50，超过上限则钳制到 200
    m = re.search(r"\blimit\s+(\d+)", cleaned, re.I)
    if not m:
        cleaned = f"{cleaned} LIMIT {DEFAULT_LIMIT}"
    elif int(m.group(1)) > MAX_LIMIT:
        cleaned = re.sub(
            rf"\blimit\s+{m.group(1)}",
            f"LIMIT {MAX_LIMIT}",
            cleaned,
            count=1,
            flags=re.I,
        )
    return cleaned
