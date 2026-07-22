"""Phase 1.2 & 1.4: dynamic prompt construction, few-shot examples, ambiguity.

Assembles the schema-aware prompt handed to the LLM, and detects questions
whose meaning is ambiguous (returning a structured clarification instead of
guessing).
"""
from __future__ import annotations

from app.models import Clarification, Interpretation
from app.schema import SchemaSnapshot

# Few-shot Q -> SQL pairs specific to this schema (Phase 1.2). They cover a
# lookup, a JOIN + categorical filter, an aggregation, a GROUP BY, and a boolean
# filter — the shapes the model needs to generalize from.
FEW_SHOTS: list[dict[str, str]] = [
    {
        "q": "How many customers do we have?",
        "sql": "SELECT COUNT(*) AS customer_count FROM customers;",
    },
    {
        "q": "List the products in the Electronics category.",
        "sql": (
            "SELECT p.name, p.price\n"
            "FROM products p\n"
            "JOIN categories c ON p.category_id = c.category_id\n"
            "WHERE c.name = 'Electronics';"
        ),
    },
    {
        "q": "What is the total gross revenue from completed orders?",
        "sql": (
            "SELECT SUM(oi.quantity * oi.unit_price) AS gross_revenue\n"
            "FROM order_items oi\n"
            "JOIN orders o ON oi.order_id = o.order_id\n"
            "WHERE o.status = 'completed';"
        ),
    },
    {
        "q": "How many orders has each customer placed?",
        "sql": (
            "SELECT c.name, COUNT(o.order_id) AS order_count\n"
            "FROM customers c\n"
            "LEFT JOIN orders o ON c.customer_id = o.customer_id\n"
            "GROUP BY c.name\n"
            "ORDER BY order_count DESC;"
        ),
    },
    {
        "q": "Which products are out of stock?",
        "sql": "SELECT name FROM products WHERE in_stock = FALSE;",
    },
]

# Ambiguous business terms -> the interpretations we surface (Phase 1.4).
_DISAMBIGUATORS = ("gross", "net", "completed", "including cancelled", "all orders", "profit", "margin")


def detect_ambiguity(question: str) -> Clarification | None:
    q = question.lower()
    if ("revenue" in q or "sales" in q) and not any(d in q for d in _DISAMBIGUATORS):
        return Clarification(
            term="revenue",
            question=(
                "\"Revenue\" is ambiguous here — should it include every order, "
                "or only completed ones? Which do you mean?"
            ),
            interpretations=[
                Interpretation(
                    label="Gross revenue (all orders)",
                    description="Sum of quantity x unit_price across every order, regardless of status.",
                    example_sql=(
                        "SELECT SUM(quantity * unit_price) AS gross_revenue FROM order_items;"
                    ),
                ),
                Interpretation(
                    label="Net revenue (completed orders only)",
                    description="Excludes cancelled, refunded, and pending orders.",
                    example_sql=(
                        "SELECT SUM(oi.quantity * oi.unit_price) AS net_revenue\n"
                        "FROM order_items oi JOIN orders o ON oi.order_id = o.order_id\n"
                        "WHERE o.status = 'completed';"
                    ),
                ),
            ],
        )
    return None


def _schema_context(question: str, snap: SchemaSnapshot) -> tuple[str, list[str]]:
    tables = snap.relevant_tables(question)
    schema_text = snap.format_for_prompt(only=tables)
    fks = snap.foreign_keys(only=tables)
    return schema_text, fks


SYSTEM_TEMPLATE = """You are a careful senior data analyst. You translate questions into a single \
read-only {dialect} SQL query.

Hard rules:
- Output SELECT queries ONLY. Never emit INSERT, UPDATE, DELETE, DROP, ALTER, CREATE, or any statement that writes.
- Use ONLY the tables and columns given in the schema below. Never invent names.
- Prefer explicit JOINs using the listed foreign keys.
- If the question cannot be answered from this schema, return an empty sql string and say so in the explanation.

Respond with ONLY a JSON object, no prose around it, of the form:
{{"sql": "<query>", "explanation": "<what it does>", "confidence": <0..1>, "tables": [<tables used>], "columns": [<columns used>]}}"""


def build_generation_prompt(
    question: str, snap: SchemaSnapshot, dialect: str = "PostgreSQL", alt: bool = False
) -> tuple[str, str]:
    schema_text, fks = _schema_context(question, snap)
    fk_block = "\n".join(fks) if fks else "(none)"
    shots = "\n\n".join(
        f"Q: {s['q']}\nJSON: {{\"sql\": {s['sql']!r}, \"explanation\": \"...\", "
        f"\"confidence\": 0.9, \"tables\": [], \"columns\": []}}"
        for s in FEW_SHOTS
    )
    user = f"""Database schema:
{schema_text}

Foreign keys:
{fk_block}

Examples:
{shots}

Now answer this question. Question: {question}"""
    system = SYSTEM_TEMPLATE.format(dialect=dialect)
    if alt:
        system += (
            "\n\nThis is an independent second attempt used for cross-validation. "
            "Deliberately use a DIFFERENT query structure than the most obvious one "
            "(e.g. a subquery or CTE instead of a plain join, or a different aggregation "
            "path) while answering the exact same question."
        )
    return system, user


BACKTRANSLATE_SYSTEM = (
    "You are given a SQL query. In one plain-English sentence, state exactly what "
    "question this query answers. Respond with only that sentence."
)


def build_backtranslation_prompt(sql: str, snap: SchemaSnapshot) -> tuple[str, str]:
    return BACKTRANSLATE_SYSTEM, f"SQL:\n{sql}\n\nWhat question does this answer?"


if __name__ == "__main__":  # self-check
    from app.schema import introspect

    snap = introspect()
    assert detect_ambiguity("what is our revenue?") is not None
    assert detect_ambiguity("total gross revenue from completed orders") is None
    assert detect_ambiguity("how many customers?") is None
    sysp, userp = build_generation_prompt("revenue by category", snap)
    assert "order_items" in userp and "Foreign keys" in userp
    assert "SELECT" in sysp or "read-only" in sysp
    print("ambiguity + prompt build OK")
