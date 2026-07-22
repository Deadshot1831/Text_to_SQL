"""Phase 2.2: guardrail middleware — the app-level safety layer.

Every generated query passes through `apply_guardrails` before it can touch the
database. Rules (all configurable via Settings):
  - single statement only (blocks stacked "…; DROP TABLE …")
  - SELECT/WITH only; any DDL or DML-write keyword is rejected
  - a row LIMIT is enforced (injected if absent)
  - subquery nesting deeper than N is rejected
  - (Postgres) queries whose EXPLAIN estimate exceeds N rows are rejected
Every blocked query is written to the audit log with its reason.
"""
from __future__ import annotations

import re

import sqlparse
from sqlalchemy import Engine, text
from sqlalchemy.exc import SQLAlchemyError
from sqlparse.sql import Parenthesis, Statement
from sqlparse.tokens import DML, Keyword

from app.audit import log_event
from app.config import get_settings
from app.models import GuardrailResult

# Any of these keyword tokens (not string literals) means "reject".
FORBIDDEN = {
    "INSERT", "UPDATE", "DELETE", "MERGE", "UPSERT", "REPLACE",
    "DROP", "CREATE", "ALTER", "TRUNCATE", "RENAME",
    "GRANT", "REVOKE", "COMMIT", "ROLLBACK",
    "CALL", "EXEC", "EXECUTE", "COPY", "ATTACH", "DETACH",
    "PRAGMA", "VACUUM", "INSTALL", "LOAD", "SET", "INTO",
}


def _statements(sql: str) -> list[Statement]:
    return [s for s in sqlparse.parse(sql) if str(s).strip() and not _is_only_ws(s)]


def _is_only_ws(stmt: Statement) -> bool:
    return all(t.is_whitespace for t in stmt.flatten())


def _forbidden_hits(stmt: Statement) -> set[str]:
    # `in Keyword` matches every keyword subtype (DML, DDL, CTE, ...); string
    # literals and identifiers are other token types and are ignored, so a
    # value like WHERE name = 'DROP TABLE' does not trip the filter.
    hits = set()
    for tok in stmt.flatten():
        if tok.ttype in Keyword and tok.value.upper() in FORBIDDEN:
            hits.add(tok.value.upper())
    return hits


def _leading_keyword(stmt: Statement) -> str:
    for tok in stmt.flatten():
        if tok.ttype in Keyword and not tok.is_whitespace:
            return tok.value.upper()
    return ""


def _contains_select(group) -> bool:
    return any(t.ttype is DML and t.value.upper() == "SELECT" for t in group.flatten())


def _subquery_depth(group, current: int = 0) -> int:
    max_d = current
    for tok in getattr(group, "tokens", []):
        if isinstance(tok, Parenthesis) and _contains_select(tok):
            max_d = max(max_d, _subquery_depth(tok, current + 1))
        elif tok.is_group:
            max_d = max(max_d, _subquery_depth(tok, current))
    return max_d


def _has_top_level_limit(stmt: Statement) -> bool:
    for tok in stmt.tokens:
        if tok.ttype is Keyword and tok.value.upper() == "LIMIT":
            return True
    return False


def _inject_limit(sql: str, limit: int) -> str:
    stripped = sql.rstrip().rstrip(";").rstrip()
    return f"{stripped}\nLIMIT {limit};"


def explain_row_estimate(engine: Engine, sql: str) -> int | None:
    """Best-effort estimated rows scanned. Reliable on Postgres; None elsewhere."""
    if engine.dialect.name != "postgresql":
        return None
    try:
        with engine.connect() as conn:
            conn.exec_driver_sql("SET TRANSACTION READ ONLY")
            row = conn.exec_driver_sql(
                f"EXPLAIN (FORMAT JSON) {sql.rstrip().rstrip(';')}"
            ).fetchone()
        plan = row[0]
        if isinstance(plan, str):
            import json

            plan = json.loads(plan)
        return int(plan[0]["Plan"]["Plan Rows"])
    except (SQLAlchemyError, KeyError, IndexError, ValueError, TypeError):
        return None


def apply_guardrails(
    sql: str, row_limit: int | None = None, engine: Engine | None = None
) -> GuardrailResult:
    s = get_settings()
    limit = row_limit or s.guardrail_default_row_limit
    violations: list[str] = []
    warnings: list[str] = []

    if not sql or not sql.strip():
        return _blocked(sql or "", ["empty query"], [])

    stmts = _statements(sql)
    if len(stmts) != 1:
        return _blocked(sql, [f"expected 1 statement, found {len(stmts)} (stacked queries are not allowed)"], [])

    stmt = stmts[0]
    lead = _leading_keyword(stmt)
    if lead not in ("SELECT", "WITH"):
        violations.append(f"only SELECT/WITH queries are allowed (got '{lead or 'unknown'}')")

    hits = _forbidden_hits(stmt)
    if hits:
        violations.append(f"forbidden keyword(s): {', '.join(sorted(hits))}")

    depth = _subquery_depth(stmt)
    if depth > s.guardrail_max_subquery_depth:
        violations.append(f"subquery nesting {depth} exceeds limit of {s.guardrail_max_subquery_depth}")

    if violations:
        return _blocked(sql, violations, warnings)

    # EXPLAIN-based scan guard (Postgres only; no-op elsewhere).
    if engine is not None:
        est = explain_row_estimate(engine, sql)
        if est is not None and est > s.guardrail_max_estimated_rows:
            return _blocked(sql, [f"estimated scan of {est} rows exceeds limit of {s.guardrail_max_estimated_rows}"], warnings)
        if est is not None:
            warnings.append(f"estimated rows scanned: {est}")

    final_sql = sql.strip()
    if not _has_top_level_limit(stmt):
        final_sql = _inject_limit(final_sql, limit)
        warnings.append(f"no LIMIT present; capped at {limit} rows")

    return GuardrailResult(allowed=True, violations=[], warnings=warnings, final_sql=final_sql)


def _blocked(sql: str, violations: list[str], warnings: list[str]) -> GuardrailResult:
    log_event("guardrail_blocked", sql=sql, violations=violations)
    return GuardrailResult(allowed=False, violations=violations, warnings=warnings, final_sql=sql)


if __name__ == "__main__":  # self-check (fuller suite in tests/test_guardrails.py)
    assert apply_guardrails("SELECT 1").allowed
    assert not apply_guardrails("DROP TABLE customers").allowed
    assert not apply_guardrails("SELECT 1; DROP TABLE customers").allowed
    assert not apply_guardrails("UPDATE customers SET name='x'").allowed
    assert not apply_guardrails("SELECT * FROM t WHERE name = 'x' INTO OUTFILE 'y'").allowed
    # string literal that looks like a keyword must NOT trip the filter
    assert apply_guardrails("SELECT * FROM products WHERE name = 'DROP TABLE'").allowed
    r = apply_guardrails("SELECT * FROM orders")
    assert "LIMIT 1000" in r.final_sql
    assert apply_guardrails("SELECT * FROM orders LIMIT 5").final_sql.strip().endswith("LIMIT 5")
    deep = "SELECT * FROM (SELECT * FROM (SELECT * FROM (SELECT * FROM (SELECT 1) a) b) c) d"
    assert not apply_guardrails(deep).allowed
    print("guardrails OK")
