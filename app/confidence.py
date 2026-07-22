"""Phase 3.4: combine the detection signals into one confidence score.

Signals (each 0..1), weighted:
  syntax_valid              0.30  did it parse + execute without error
  backtranslation_alignment 0.25  does the SQL answer the question asked
  sanity_pass_rate          0.15  did the results look plausible
  schema_coverage           0.15  do all referenced tables really exist
  multiquery_agreement      0.15  (only when requested) do two strategies agree

When multi-query isn't run its weight is dropped and the rest re-normalized.
"""
from __future__ import annotations

from app.generate import referenced_tables
from app.models import ConfidenceBreakdown
from app.schema import SchemaSnapshot

_WEIGHTS = {
    "syntax_valid": 0.30,
    "backtranslation_alignment": 0.25,
    "sanity_pass_rate": 0.15,
    "schema_coverage": 0.15,
    "multiquery_agreement": 0.15,
}


def schema_coverage(sql: str, snap: SchemaSnapshot) -> float:
    refs = referenced_tables(sql)
    if not refs:
        return 0.0
    known = sum(1 for t in refs if t in snap.tables)
    return round(known / len(refs), 3)


def compute_confidence(
    *,
    syntax_valid: bool,
    backtranslation_alignment: float,
    sanity_pass_rate: float,
    schema_coverage: float,
    multiquery_agreement: bool | None,
) -> ConfidenceBreakdown:
    values = {
        "syntax_valid": 1.0 if syntax_valid else 0.0,
        "backtranslation_alignment": backtranslation_alignment,
        "sanity_pass_rate": sanity_pass_rate,
        "schema_coverage": schema_coverage,
    }
    mq = None
    if multiquery_agreement is not None:
        mq = 1.0 if multiquery_agreement else 0.0
        values["multiquery_agreement"] = mq

    total_w = sum(_WEIGHTS[k] for k in values)
    overall = round(sum(values[k] * _WEIGHTS[k] for k in values) / total_w, 3)

    return ConfidenceBreakdown(
        syntax_valid=values["syntax_valid"],
        backtranslation_alignment=backtranslation_alignment,
        sanity_pass_rate=sanity_pass_rate,
        schema_coverage=schema_coverage,
        multiquery_agreement=mq,
        overall=overall,
    )


if __name__ == "__main__":  # self-check
    from app.schema import introspect

    snap = introspect()
    assert schema_coverage("SELECT * FROM orders JOIN customers ON 1=1", snap) == 1.0
    assert schema_coverage("SELECT * FROM nonexistent", snap) == 0.0
    high = compute_confidence(syntax_valid=True, backtranslation_alignment=1.0, sanity_pass_rate=1.0, schema_coverage=1.0, multiquery_agreement=True)
    low = compute_confidence(syntax_valid=False, backtranslation_alignment=0.1, sanity_pass_rate=0.3, schema_coverage=0.0, multiquery_agreement=None)
    assert high.overall > 0.9 and low.overall < 0.3, (high.overall, low.overall)
    print(f"confidence OK — high={high.overall} low={low.overall}")
