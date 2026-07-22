"""Phase 3: hallucination detection.

Three independent signals that a query may not answer the question asked:
  3.1 back-translation — describe the SQL, compare to the original question
  3.2 result sanity   — implausible aggregates, NULL-heavy columns, empty joins
  3.3 multi-query     — a second, differently-phrased query; do results agree?
"""
from __future__ import annotations

import re

from sqlalchemy import Engine

from app.execute import run_query
from app.guardrails import apply_guardrails
from app.llm import complete, parse_json_block
from app.models import ExecutionResult, MultiQueryResult, SanityFlag
from app.prompt import build_backtranslation_prompt, build_generation_prompt
from app.schema import SchemaSnapshot

ALIGNMENT_FLAG_THRESHOLD = 0.4

_STOP = {
    "the", "a", "an", "of", "to", "in", "on", "for", "and", "or", "is", "are",
    "do", "we", "our", "how", "what", "which", "that", "this", "with", "by",
    "from", "as", "have", "has", "was", "were", "be", "there", "me", "us",
    "query", "returns", "return", "records", "record", "database", "answers",
    "show", "list", "give", "get", "please",
}
# Domain synonyms collapsed to a canonical token so paraphrases still align.
_SYN = {
    "number": "count", "many": "count", "total": "sum", "sales": "revenue",
    "per": "group", "each": "group", "grouped": "group", "average": "avg",
    "mean": "avg", "customer": "customers", "order": "orders",
    "product": "products", "categori": "category", "categories": "category",
}


def _canon(word: str) -> str:
    w = re.sub(r"[^a-z0-9]", "", word.lower())
    if w in _SYN:
        return _SYN[w]
    if len(w) > 4 and w.endswith("s"):
        w = w[:-1]
    return _SYN.get(w, w)


def _content_tokens(text: str) -> set[str]:
    toks = {_canon(w) for w in re.split(r"\s+", text) if w}
    return {t for t in toks if t and t not in _STOP and len(t) > 1}


def alignment(original: str, backtranslated: str) -> float:
    """0..1 overlap between the question and the SQL's back-translation."""
    a, b = _content_tokens(original), _content_tokens(backtranslated)
    if not a or not b:
        return 0.0
    inter = len(a & b)
    jaccard = inter / len(a | b)
    recall = inter / len(a)  # how much of the question's meaning survived
    return round(0.5 * jaccard + 0.5 * recall, 3)


def back_translate(sql: str, snap: SchemaSnapshot) -> str:
    system, user = build_backtranslation_prompt(sql, snap)
    return complete(system, user, max_tokens=120).strip()


def sanity_checks(question: str, execution: ExecutionResult, sql: str) -> list[SanityFlag]:
    flags: list[SanityFlag] = []
    if execution.error:
        flags.append(SanityFlag(check="execution", severity="warn", message=f"query errored: {execution.error}"))
        return flags

    if execution.row_count == 0:
        sev = "warn" if " join " in f" {sql.lower()} " else "info"
        flags.append(SanityFlag(check="empty_result", severity=sev, message="query returned no rows"))

    # NULL-heavy columns often signal a bad JOIN.
    for j, col in enumerate(execution.columns):
        if execution.row_count:
            nulls = sum(1 for row in execution.rows if row[j] is None)
            if nulls / execution.row_count > 0.5:
                flags.append(SanityFlag(check="null_heavy", severity="warn", message=f"column '{col}' is >50% NULL — possible bad join"))

    # Aggregate columns should not be negative.
    for j, col in enumerate(execution.columns):
        if any(k in col.lower() for k in ("revenue", "total", "count", "sum", "avg", "amount")):
            for row in execution.rows:
                v = row[j]
                if isinstance(v, (int, float)) and v < 0:
                    flags.append(SanityFlag(check="negative_aggregate", severity="warn", message=f"aggregate '{col}' is negative ({v})"))
                    break

    if not flags:
        flags.append(SanityFlag(check="basic", severity="info", message="no anomalies detected"))
    return flags


def sanity_pass_rate(flags: list[SanityFlag]) -> float:
    if not flags:
        return 1.0
    warns = sum(1 for f in flags if f.severity == "warn")
    return round(1.0 - warns / len(flags), 3)


def multi_query_check(
    question: str,
    primary_sql: str,
    primary_exec: ExecutionResult,
    snap: SchemaSnapshot,
    engine: Engine,
    dialect: str,
) -> MultiQueryResult:
    """Generate an independent second query and compare results (Phase 3.3)."""
    system, user = build_generation_prompt(question, snap, dialect, alt=True)
    try:
        data = parse_json_block(complete(system, user))
    except Exception as e:  # noqa: BLE001 - any LLM/parse failure is just "no second opinion"
        return MultiQueryResult(alt_sql="", agree=False, note=f"alt generation failed: {e}")
    alt_sql = (data.get("sql") or "").strip()

    if alt_sql and alt_sql.strip() == primary_sql.strip():
        agree = True
        note = "second attempt produced identical SQL (no independent strategy available offline)"
        return MultiQueryResult(alt_sql=alt_sql, agree=agree, note=note)

    guard = apply_guardrails(alt_sql, engine=engine)
    if not guard.allowed:
        return MultiQueryResult(alt_sql=alt_sql, agree=False, note="alternative query blocked by guardrails")
    alt_exec = run_query(guard.final_sql, engine=engine)
    if alt_exec.error:
        return MultiQueryResult(alt_sql=alt_sql, agree=False, note=f"alternative query failed: {alt_exec.error}")

    agree = _same_results(primary_exec, alt_exec)
    return MultiQueryResult(
        alt_sql=alt_sql,
        agree=agree,
        note="results agree across two strategies" if agree else "results differ — review both",
    )


def _same_results(a: ExecutionResult, b: ExecutionResult) -> bool:
    def canon(ex: ExecutionResult):
        norm = []
        for row in ex.rows:
            norm.append(tuple(round(c, 2) if isinstance(c, float) else str(c) for c in row))
        return sorted(norm, key=str)

    return canon(a) == canon(b)


if __name__ == "__main__":  # self-check
    from app.schema import introspect

    snap = introspect()
    # correct pairing aligns better than a wrong one
    good = alignment("how many customers do we have", back_translate("SELECT COUNT(*) FROM customers", snap))
    bad = alignment("how many customers do we have", back_translate("SELECT SUM(quantity*unit_price) FROM order_items oi", snap))
    assert good > bad, (good, bad)
    assert good >= ALIGNMENT_FLAG_THRESHOLD, good
    ex = run_query("SELECT COUNT(*) AS n FROM customers")
    assert sanity_pass_rate(sanity_checks("count customers", ex, "SELECT COUNT(*) FROM customers")) == 1.0
    print(f"validation OK — good_align={good} bad_align={bad}")
