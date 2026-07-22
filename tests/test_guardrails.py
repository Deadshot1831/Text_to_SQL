"""Guardrail safety tests — the security boundary. Every write/DDL must be blocked."""
import pytest

from app.guardrails import apply_guardrails

SAFE = [
    "SELECT * FROM orders",
    "SELECT count(*) FROM customers",
    "WITH t AS (SELECT 1 AS x) SELECT x FROM t",
    "SELECT * FROM products WHERE name = 'DROP TABLE'",  # keyword only in a literal
    "SELECT * FROM (SELECT * FROM (SELECT * FROM orders) a) b",  # depth 2, allowed
]

DANGEROUS = [
    "DROP TABLE customers",
    "DELETE FROM orders",
    "INSERT INTO orders VALUES (1)",
    "UPDATE customers SET name = 'x'",
    "TRUNCATE customers",
    "ALTER TABLE orders ADD COLUMN x INT",
    "CREATE TABLE evil (id INT)",
    "GRANT ALL ON orders TO public",
    "SELECT * FROM orders; DROP TABLE orders",  # stacked
    "SELECT * INTO backup FROM orders",  # SELECT INTO writes
    "COPY orders TO '/tmp/x.csv'",
    "",  # empty
]


@pytest.mark.parametrize("sql", SAFE)
def test_safe_queries_allowed(sql):
    assert apply_guardrails(sql).allowed, f"should be allowed: {sql}"


@pytest.mark.parametrize("sql", DANGEROUS)
def test_dangerous_queries_blocked(sql):
    r = apply_guardrails(sql)
    assert not r.allowed, f"should be BLOCKED: {sql}"
    assert r.violations, "a blocked query must report a reason"


def test_limit_injected_when_absent():
    r = apply_guardrails("SELECT * FROM orders")
    assert "LIMIT 1000" in r.final_sql
    assert any("LIMIT" in w for w in r.warnings)


def test_existing_limit_preserved():
    r = apply_guardrails("SELECT * FROM orders LIMIT 5")
    assert r.final_sql.strip().rstrip(";").endswith("LIMIT 5")


def test_subquery_depth_boundary():
    depth3 = "SELECT * FROM (SELECT * FROM (SELECT * FROM (SELECT 1) a) b) c"
    depth4 = f"SELECT * FROM ({depth3}) d"
    assert apply_guardrails(depth3).allowed
    assert not apply_guardrails(depth4).allowed


def test_custom_row_limit():
    r = apply_guardrails("SELECT * FROM orders", row_limit=10)
    assert "LIMIT 10" in r.final_sql
