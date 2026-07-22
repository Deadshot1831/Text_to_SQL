"""Phase 2.1: structured SQL generation.

Asks the LLM for {sql, explanation, confidence, tables, columns} as JSON, parses
it into a GeneratedSQL, and does a cheap well-formedness check with sqlparse
before the query goes anywhere near the guardrails or the database.
"""
from __future__ import annotations

import sqlparse
from sqlparse.tokens import DML, Keyword

from app.llm import complete, parse_json_block
from app.models import GeneratedSQL
from app.prompt import build_generation_prompt
from app.schema import SchemaSnapshot


def is_wellformed(sql: str) -> bool:
    """Cheap syntactic gate: parses, balanced parens, leads with SELECT/WITH."""
    if not sql or not sql.strip():
        return False
    if sql.count("(") != sql.count(")"):
        return False
    parsed = sqlparse.parse(sql)
    if not parsed:
        return False
    for tok in parsed[0].flatten():
        if tok.ttype in (DML, Keyword) and not tok.is_whitespace:
            return tok.value.upper() in ("SELECT", "WITH")
    return False


def generate_sql(
    question: str, snap: SchemaSnapshot, dialect: str = "PostgreSQL"
) -> GeneratedSQL:
    system, user = build_generation_prompt(question, snap, dialect)
    raw = complete(system, user)
    data = parse_json_block(raw)
    try:
        conf = float(data.get("confidence", 0.5))
    except (TypeError, ValueError):
        conf = 0.5
    return GeneratedSQL(
        sql=(data.get("sql") or "").strip(),
        explanation=data.get("explanation", ""),
        confidence=min(max(conf, 0.0), 1.0),
        tables=data.get("tables") or [],
        columns=data.get("columns") or [],
    )


if __name__ == "__main__":  # self-check (uses stub provider offline)
    from app.schema import introspect

    snap = introspect()
    g = generate_sql("how many customers do we have?", snap)
    assert "COUNT" in g.sql.upper() and is_wellformed(g.sql), g
    assert is_wellformed("SELECT 1")
    assert not is_wellformed("SELECT (1")  # unbalanced
    assert not is_wellformed("DELETE FROM t")  # not a read
    print("generate OK:", g.sql)
