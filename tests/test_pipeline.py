"""End-to-end pipeline behavior (stub provider)."""
from app.models import QueryRequest
from app.pipeline import answer


def test_normal_query_ok():
    r = answer(QueryRequest(question="how many customers do we have?"))
    assert r.status == "ok"
    assert r.execution.rows and r.execution.rows[0][0] == 12
    assert r.confidence.overall > 0.8
    assert r.validation.backtranslation


def test_ambiguous_returns_clarification():
    r = answer(QueryRequest(question="what is our revenue?"))
    assert r.status == "clarification_needed"
    assert r.clarification and len(r.clarification.interpretations) == 2


def test_unanswerable_returns_error():
    r = answer(QueryRequest(question="what was the weather during each order?"))
    assert r.status == "error"


def test_edited_dangerous_sql_blocked():
    r = answer(QueryRequest(question="remove customers", sql_override="DROP TABLE customers"))
    assert r.status == "blocked"
    assert r.guardrail and not r.guardrail.allowed


def test_hallucination_low_alignment_flagged():
    # SQL answers a different question than asked -> low back-translation alignment.
    r = answer(QueryRequest(
        question="how many customers do we have?",
        sql_override="SELECT SUM(oi.quantity * oi.unit_price) AS gross_revenue FROM order_items oi",
    ))
    assert r.validation.backtranslation_alignment < 0.4
    assert any("alignment" in w for w in r.warnings)
    assert r.confidence.overall < 0.75  # dragged down by the mismatch


def test_entity_mismatch_flagged():
    # Question is about customers, but the SQL only touches orders.
    r = answer(QueryRequest(
        question="how many customers do we have?",
        sql_override="SELECT COUNT(*) FROM orders",
    ))
    assert any(f.check == "entity_mismatch" for f in r.validation.sanity_flags)
    assert any("customers" in w for w in r.warnings)


def test_guardrail_injects_limit_in_response():
    r = answer(QueryRequest(question="list all products"))
    assert "LIMIT" in r.guardrail.final_sql.upper()
