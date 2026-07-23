"""Scripted demo of the text-to-SQL pipeline (offline stub; no API key needed).

Run:  python demo.py
Shows: NL->SQL, ambiguity clarification, a guardrail block, hallucination
detection, and multi-query cross-validation.
"""
from __future__ import annotations

from app.models import QueryRequest
from app.pipeline import answer

LINE = "─" * 68


def show(title: str, req: QueryRequest) -> None:
    r = answer(req)
    print(f"\n{LINE}\n▶ {title}\n{LINE}")
    print(f"Question: {req.question}")
    if req.sql_override:
        print(f"Edited SQL submitted: {req.sql_override}")
    print(f"Status:   {r.status.upper()}")

    if r.status == "clarification_needed":
        print(f"\n🤔 {r.clarification.question}")
        for i in r.clarification.interpretations:
            print(f"   • {i.label}: {i.description}")
        return
    if r.status == "blocked":
        print("\n⛔ Blocked by guardrails:")
        for v in r.guardrail.violations:
            print(f"   - {v}")
        return
    if r.status == "error":
        print(f"\n⚠️  {r.error}")
        return

    print(f"\nSQL:\n   {r.guardrail.final_sql.strip().splitlines()[0]} ...")
    if r.execution.columns:
        print(f"Result:   {r.execution.columns} -> {r.execution.rows[:3]}"
              f"{' …' if r.execution.row_count > 3 else ''}")
    print(f"Back-translation: {r.validation.backtranslation}")
    if r.validation.multiquery:
        icon = "✅" if r.validation.multiquery.agree else "⚠️"
        print(f"Multi-query:      {icon} {r.validation.multiquery.note}")
    print(f"Confidence:       {r.confidence.overall:.0%}  "
          f"(align={r.confidence.backtranslation_alignment:.2f}, "
          f"sanity={r.confidence.sanity_pass_rate:.2f}, "
          f"schema={r.confidence.schema_coverage:.2f})")
    for w in r.warnings:
        print(f"⚠️  {w}")


def main() -> None:
    print("\n" + "=" * 68)
    print("  TEXT-TO-SQL WITH GUARDRAILS & HALLUCINATION DETECTION — DEMO")
    print("=" * 68)

    show("1. Natural language -> SQL (aggregation across 3 joined tables)",
         QueryRequest(question="What is the gross revenue by category?"))

    show("2. Ambiguity: refuse to guess, ask instead",
         QueryRequest(question="What is our revenue?"))

    show("3. Guardrail blocks a destructive edited query",
         QueryRequest(question="clean up old records", sql_override="DROP TABLE customers"))

    show("4. Hallucination caught: SQL answers the wrong question",
         QueryRequest(question="How many customers do we have?",
                      sql_override="SELECT COUNT(*) FROM orders"))

    show("5. Multi-query cross-validation",
         QueryRequest(question="How many orders are completed?", multi_query=True))

    show("6. Unanswerable question is refused (not hallucinated)",
         QueryRequest(question="What was the weather on each order date?"))

    print(f"\n{LINE}\nRun `python -m evals.run_evals` for the full evaluation report.\n")


if __name__ == "__main__":
    main()
