"""End-to-end orchestrator: question -> clarification? -> SQL -> guardrails ->
execute -> validate -> confidence -> response. The API and the Streamlit UI both
call `answer`.
"""
from __future__ import annotations

from functools import lru_cache

from app.audit import log_event
from app.confidence import compute_confidence, schema_coverage
from app.config import get_settings
from app.db import dialect_name, get_engine
from app.execute import run_query
from app.generate import generate_sql
from app.guardrails import apply_guardrails
from app.injection import detect_injection
from app.models import GeneratedSQL, GuardrailResult, QueryRequest, QueryResponse, ValidationReport
from app.prompt import detect_ambiguity
from app.schema import SchemaSnapshot, introspect
from app.validation import (
    ALIGNMENT_FLAG_THRESHOLD,
    alignment,
    back_translate,
    multi_query_check,
    sanity_checks,
    sanity_pass_rate,
)

_DIALECT_LABEL = {"postgresql": "PostgreSQL", "duckdb": "DuckDB"}


@lru_cache
def _snapshot_for(database_url: str) -> SchemaSnapshot:
    return introspect(get_engine())


def get_snapshot() -> SchemaSnapshot:
    return _snapshot_for(get_settings().database_url)


def answer(req: QueryRequest) -> QueryResponse:
    q = req.question.strip()
    engine = get_engine()
    snap = get_snapshot()
    dialect = _DIALECT_LABEL.get(dialect_name(engine), dialect_name(engine))

    if not q:
        return QueryResponse(question=q, status="error", error="empty question")

    # Security — screen for prompt / SQL injection before the question reaches the LLM.
    inj = detect_injection(q)
    if inj:
        log_event("prompt_injection_blocked", question=q, reasons=", ".join(inj))
        return QueryResponse(
            question=q, status="blocked",
            guardrail=GuardrailResult(allowed=False, violations=inj, final_sql=""),
            error="; ".join(inj),
        )

    # Phase 1.4 — refuse to guess when the question is ambiguous.
    clar = detect_ambiguity(q)
    if clar:
        log_event("clarification", question=q, term=clar.term)
        return QueryResponse(question=q, status="clarification_needed", clarification=clar)

    # Phase 2.1 — generate structured SQL (or accept a power-user's edited SQL).
    if req.sql_override and req.sql_override.strip():
        gen = GeneratedSQL(sql=req.sql_override.strip(), explanation="user-supplied SQL", confidence=1.0)
    else:
        gen = generate_sql(q, snap, dialect)
    if not gen.sql:
        return QueryResponse(
            question=q, status="error", generated=gen,
            error=gen.explanation or "no SQL produced — the question may be unanswerable from this schema",
        )

    # Phase 2.2 — guardrails.
    guard = apply_guardrails(gen.sql, row_limit=req.row_limit, engine=engine)
    if not guard.allowed:
        return QueryResponse(
            question=q, status="blocked", generated=gen, guardrail=guard,
            error="; ".join(guard.violations), warnings=guard.warnings,
        )

    # Phase 2.4 — execute in the read-only sandbox.
    execution = run_query(guard.final_sql, row_limit=req.row_limit, engine=engine)

    # Phase 3 — hallucination detection.
    bt = back_translate(guard.final_sql, snap)
    align = alignment(q, bt)
    flags = sanity_checks(q, execution, guard.final_sql, snap)
    spr = sanity_pass_rate(flags)

    mq_result = None
    mq_agree = None
    if req.multi_query and execution.error is None:
        # Compare against the pre-guardrail SQL so an offline stub that reproduces
        # the same query is disclosed honestly instead of looking like two strategies.
        mq_result = multi_query_check(q, gen.sql, execution, snap, engine, dialect)
        mq_agree = mq_result.agree

    validation = ValidationReport(
        backtranslation=bt,
        backtranslation_alignment=align,
        sanity_flags=flags,
        multiquery=mq_result,
    )

    # Phase 3.4 — confidence.
    confidence = compute_confidence(
        syntax_valid=execution.error is None,
        backtranslation_alignment=align,
        sanity_pass_rate=spr,
        schema_coverage=schema_coverage(guard.final_sql, snap),
        multiquery_agreement=mq_agree,
    )

    warnings = list(guard.warnings)
    if align < ALIGNMENT_FLAG_THRESHOLD:
        warnings.append(f"low back-translation alignment ({align}) — SQL may not answer the question")
    warnings += [f.message for f in flags if f.severity == "warn"]
    if mq_result and not mq_result.agree:
        warnings.append(f"multi-query check: {mq_result.note}")

    status = "ok" if execution.error is None else "error"
    log_event("answered", question=q, sql=guard.final_sql, status=status, confidence=confidence.overall)

    return QueryResponse(
        question=q, status=status, generated=gen, guardrail=guard, execution=execution,
        validation=validation, confidence=confidence, warnings=warnings, error=execution.error,
    )


if __name__ == "__main__":  # smoke test (stub provider)
    r = answer(QueryRequest(question="how many customers do we have?"))
    assert r.status == "ok" and r.confidence.overall > 0.8
    print("answer   ->", r.status, "| sql:", r.generated.sql, "| confidence:", r.confidence.overall)
    amb = answer(QueryRequest(question="what is our revenue?"))
    assert amb.status == "clarification_needed"
    print("ambiguous->", amb.status, "|", amb.clarification.term)
    oos = answer(QueryRequest(question="what was the weather on each order date?"))
    print("no-answer->", oos.status, "|", oos.error)
    blk = answer(QueryRequest(question="delete everything", sql_override="DROP TABLE customers"))
    assert blk.status == "blocked"
    print("edited   ->", blk.status, "|", blk.error)
