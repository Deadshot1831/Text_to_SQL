"""Phase 2.3 & 2.4: read-only sandboxed execution.

Runs the validated query inside a transaction that is always rolled back, on a
connection set READ ONLY where the engine supports it (Postgres). Combined with
the guardrails and the SELECT-only DB role, this is defense in depth: three
independent layers must all fail for a write to land.
"""
from __future__ import annotations

import time
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import Engine, text
from sqlalchemy.exc import SQLAlchemyError

from app.config import get_settings
from app.db import get_engine
from app.models import ExecutionResult


def _jsonable(v):
    if isinstance(v, Decimal):
        return float(v)
    if isinstance(v, (date, datetime)):
        return v.isoformat()
    if isinstance(v, (bool, int, float, str)) or v is None:
        return v
    return str(v)


def _begin_readonly(conn) -> None:
    """Start a read-only transaction (Postgres) with a statement timeout."""
    if conn.engine.dialect.name == "postgresql":
        ms = get_settings().guardrail_statement_timeout_ms
        conn.exec_driver_sql("SET TRANSACTION READ ONLY")
        conn.exec_driver_sql(f"SET statement_timeout = {int(ms)}")


def capture_explain(engine: Engine, sql: str) -> str | None:
    try:
        with engine.connect() as conn:
            _begin_readonly(conn)
            rows = conn.exec_driver_sql(f"EXPLAIN {sql.rstrip().rstrip(';')}").fetchall()
            conn.rollback()
        return "\n".join(str(r[0]) for r in rows)
    except SQLAlchemyError:
        return None


def run_query(sql: str, row_limit: int | None = None, engine: Engine | None = None) -> ExecutionResult:
    engine = engine or get_engine()
    limit = row_limit or get_settings().guardrail_default_row_limit
    t0 = time.perf_counter()
    try:
        with engine.connect() as conn:
            _begin_readonly(conn)
            result = conn.execute(text(sql))
            columns = list(result.keys())
            fetched = result.fetchmany(limit + 1)  # one extra row detects truncation
            conn.rollback()  # never persist; read-only sandbox
    except SQLAlchemyError as e:
        return ExecutionResult(error=str(e).splitlines()[0], execution_ms=(time.perf_counter() - t0) * 1000)

    elapsed = (time.perf_counter() - t0) * 1000
    truncated = len(fetched) > limit
    rows = [[_jsonable(c) for c in row] for row in fetched[:limit]]
    return ExecutionResult(
        columns=columns,
        rows=rows,
        row_count=len(rows),
        truncated=truncated,
        execution_ms=round(elapsed, 2),
        explain_plan=capture_explain(engine, sql),
    )


if __name__ == "__main__":  # self-check
    r = run_query("SELECT COUNT(*) AS n FROM orders")
    assert r.error is None and r.rows and r.rows[0][0] == 30, r
    # a syntactically valid query against a missing column returns an error, not a crash
    bad = run_query("SELECT nope FROM orders")
    assert bad.error is not None
    print("execute OK — orders:", r.rows[0][0], "| explain captured:", bool(r.explain_plan))
