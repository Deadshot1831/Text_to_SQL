"""Deterministic offline fallback provider for the demo schema.

Not a real text-to-SQL model — a keyword/template matcher good enough to run the
full pipeline (guardrails, execution, validation, confidence, evals) and the demo
without any API key. `describe_sql` faithfully back-translates whatever SQL it is
given, so the hallucination detector still works against it.

ponytail: pattern matcher, not a model. Set ANTHROPIC_API_KEY (or LLM_PROVIDER)
to route generation through a real LLM; this file is only the offline path.
"""
from __future__ import annotations

import json
import re

CATEGORIES = ["Electronics", "Books", "Clothing", "Home", "Toys"]
COUNTRIES = ["USA", "UK", "Canada", "Germany", "India", "Australia"]
STATUSES = ["completed", "pending", "cancelled", "refunded"]
# Concepts the demo schema genuinely cannot answer.
OUT_OF_SCHEMA = ["weather", "supplier", "employee", "rating", "review", "phone", "shipping address", "discount code"]

_REVENUE_EXPR = "SUM(oi.quantity * oi.unit_price)"


def stub_response(system: str, user: str) -> str:
    if "state exactly what question" in system.lower():
        sql = user.split("SQL:", 1)[-1].split("What question")[0].strip()
        return describe_sql(sql)
    question = user.split("Question:")[-1].strip()
    sql, expl, tables, cols, conf = question_to_sql(question)
    return json.dumps(
        {"sql": sql, "explanation": expl, "confidence": conf, "tables": tables, "columns": cols}
    )


def _find(options: list[str], q: str) -> str | None:
    for o in options:
        if o.lower() in q:
            return o
    return None


def question_to_sql(question: str) -> tuple[str, str, list[str], list[str], float]:
    q = question.lower().strip()
    ym = re.search(r"\b(20\d{2})\b", q)
    year = ym.group(1) if ym else None
    status = _find(STATUSES, q)
    category = _find(CATEGORIES, q)
    country = _find(COUNTRIES, q)

    # --- questions the schema cannot answer ---
    oos = _find(OUT_OF_SCHEMA, q)
    if oos:
        return "", f"The schema has no {oos} data, so this question cannot be answered.", [], [], 0.5

    # --- most expensive / cheapest product (before the generic 'top products') ---
    if "product" in q and ("most expensive" in q or "highest price" in q):
        return "SELECT name, price FROM products ORDER BY price DESC LIMIT 1;", "The most expensive product.", ["products"], ["name", "price"], 0.7
    if "product" in q and ("cheapest" in q or "lowest price" in q):
        return "SELECT name, price FROM products ORDER BY price ASC LIMIT 1;", "The cheapest product.", ["products"], ["name", "price"], 0.7

    # --- top / best products (before the revenue family so "top … by revenue" routes here) ---
    if ("top" in q or "best" in q or "most" in q) and "product" in q:
        by_rev = "revenue" in q or "money" in q or "sales" in q
        metric, alias = (_REVENUE_EXPR, "revenue") if by_rev else ("SUM(oi.quantity)", "units_sold")
        sql = (
            f"SELECT p.name, {metric} AS {alias}\n"
            "FROM order_items oi JOIN products p ON oi.product_id = p.product_id\n"
            f"GROUP BY p.name\nORDER BY {alias} DESC\nLIMIT 5;"
        )
        return sql, f"Top 5 products by {alias}.", ["order_items", "products"], ["quantity", "unit_price", "name"], 0.6

    # --- revenue family (ambiguity already handled upstream) ---
    if "revenue" in q or "sales" in q:
        if "categor" in q:
            sql = (
                f"SELECT c.name AS category, {_REVENUE_EXPR} AS revenue\n"
                "FROM order_items oi\n"
                "JOIN products p ON oi.product_id = p.product_id\n"
                "JOIN categories c ON p.category_id = c.category_id\n"
                "GROUP BY c.name\nORDER BY revenue DESC;"
            )
            return sql, "Revenue per product category.", ["order_items", "products", "categories"], ["quantity", "unit_price", "name"], 0.65
        if "net" in q or "completed" in q:
            sql = (
                f"SELECT {_REVENUE_EXPR} AS net_revenue\n"
                "FROM order_items oi\nJOIN orders o ON oi.order_id = o.order_id\n"
                "WHERE o.status = 'completed';"
            )
            return sql, "Net revenue from completed orders only.", ["order_items", "orders"], ["quantity", "unit_price", "status"], 0.65
        sql = f"SELECT {_REVENUE_EXPR} AS gross_revenue\nFROM order_items oi;"
        return sql, "Gross revenue across all orders.", ["order_items"], ["quantity", "unit_price"], 0.6

    # --- average order value ---
    if "average" in q and "order" in q and ("value" in q or "size" in q):
        sql = (
            "SELECT AVG(order_total) AS avg_order_value FROM (\n"
            f"  SELECT o.order_id, {_REVENUE_EXPR} AS order_total\n"
            "  FROM orders o JOIN order_items oi ON o.order_id = oi.order_id\n"
            "  GROUP BY o.order_id) t;"
        )
        return sql, "Average total value per order.", ["orders", "order_items"], ["quantity", "unit_price"], 0.6

    # --- total quantity sold ---
    if ("quantity" in q or "items sold" in q or "units sold" in q) and ("total" in q or "how many" in q or "sum" in q):
        return "SELECT SUM(quantity) AS total_quantity FROM order_items;", "Total quantity of items sold.", ["order_items"], ["quantity"], 0.65

    # --- stock ---
    if "out of stock" in q or ("not" in q and "stock" in q):
        return "SELECT name FROM products WHERE in_stock = FALSE;", "Products that are out of stock.", ["products"], ["name", "in_stock"], 0.7
    if "in stock" in q:
        return "SELECT name FROM products WHERE in_stock = TRUE;", "Products currently in stock.", ["products"], ["name", "in_stock"], 0.7

    # --- products in a category ---
    if "product" in q and category:
        sql = (
            "SELECT p.name, p.price\nFROM products p\n"
            "JOIN categories c ON p.category_id = c.category_id\n"
            f"WHERE c.name = '{category}';"
        )
        return sql, f"Products in the {category} category.", ["products", "categories"], ["name", "price"], 0.7

    # --- customers ---
    if "customer" in q and ("never" in q or "no order" in q or "without" in q):
        sql = (
            "SELECT c.name FROM customers c\n"
            "LEFT JOIN orders o ON c.customer_id = o.customer_id\n"
            "WHERE o.order_id IS NULL;"
        )
        return sql, "Customers who have never placed an order.", ["customers", "orders"], ["name"], 0.6
    if "customer" in q and ("per country" in q or "by country" in q or "each country" in q):
        sql = "SELECT country, COUNT(*) AS customer_count FROM customers GROUP BY country ORDER BY customer_count DESC;"
        return sql, "Customer count per country.", ["customers"], ["country"], 0.65
    if "customer" in q and country:
        return f"SELECT name, email FROM customers WHERE country = '{country}';", f"Customers from {country}.", ["customers"], ["name", "email", "country"], 0.7

    # --- orders per customer ---
    if "order" in q and ("each" in q or "per" in q) and "customer" in q:
        sql = (
            "SELECT c.name, COUNT(o.order_id) AS order_count\n"
            "FROM customers c LEFT JOIN orders o ON c.customer_id = o.customer_id\n"
            "GROUP BY c.name\nORDER BY order_count DESC;"
        )
        return sql, "Number of orders per customer.", ["customers", "orders"], ["name"], 0.65

    # --- counts (\bcount\b so 'country' does not match) ---
    if "how many" in q or "number of" in q or re.search(r"\bcount\b", q):
        if "customer" in q:
            return "SELECT COUNT(*) AS customer_count FROM customers;", "Total number of customers.", ["customers"], [], 0.75
        if "product" in q:
            return "SELECT COUNT(*) AS product_count FROM products;", "Total number of products.", ["products"], [], 0.75
        if "order" in q:
            conds = []
            if status:
                conds.append(f"status = '{status}'")
            if year:
                conds.append(f"EXTRACT(YEAR FROM order_date) = {year}")
            where = (" WHERE " + " AND ".join(conds)) if conds else ""
            return f"SELECT COUNT(*) AS order_count FROM orders{where};", "Number of orders.", ["orders"], ["status", "order_date"], 0.7

    # --- orders by status / year (list) ---
    if "order" in q and (status or year):
        conds = []
        if status:
            conds.append(f"status = '{status}'")
        if year:
            conds.append(f"EXTRACT(YEAR FROM order_date) = {year}")
        where = " WHERE " + " AND ".join(conds)
        return f"SELECT order_id, customer_id, order_date, status FROM orders{where} ORDER BY order_date;", "Matching orders.", ["orders"], ["status", "order_date"], 0.6

    # --- list tables ---
    if "product" in q and ("list" in q or "all" in q or "show" in q):
        return "SELECT name, price FROM products ORDER BY name;", "All products.", ["products"], ["name", "price"], 0.6
    if "customer" in q and ("list" in q or "all" in q or "show" in q):
        return "SELECT name, country FROM customers ORDER BY name;", "All customers.", ["customers"], ["name", "country"], 0.6

    # --- low-confidence fallback ---
    return "SELECT * FROM orders LIMIT 20;", "Fallback: could not confidently map the question.", ["orders"], [], 0.2


_STOP = {"the", "of", "from", "and", "for", "each", "all", "by", "in", "on", "as", "select", "join"}


def describe_sql(sql: str) -> str:
    s = sql.lower()
    if not s.strip():
        return "This query is empty and answers nothing."
    if "count(" in s:
        agg = "the number of records"
    elif "sum(" in s and "quantity" in s and "unit_price" in s:
        agg = "the total revenue"
    elif "avg(" in s:
        agg = "the average value"
    elif "sum(" in s:
        agg = "a total"
    else:
        agg = "records"
    tables = []
    for m in re.finditer(r"\b(?:from|join)\s+([a-z_]+)", s):
        if m.group(1) not in tables:
            tables.append(m.group(1))
    parts = [f"This query returns {agg}", f"from {', '.join(tables) or 'the database'}"]
    st = re.search(r"status\s*=\s*'(\w+)'", s)
    if st:
        parts.append(f"for {st.group(1)} orders")
    cat = re.search(r"c\.name\s*=\s*'([\w ]+)'", s)
    if cat:
        parts.append(f"in the {cat.group(1).strip()} category")
    if "in_stock = false" in s:
        parts.append("that are out of stock")
    elif "in_stock = true" in s:
        parts.append("that are in stock")
    ctry = re.search(r"country\s*=\s*'(\w+)'", s)
    if ctry:
        parts.append(f"from {ctry.group(1)}")
    yr = re.search(r"year from \w+\)\s*=\s*(\d{4})", s)
    if yr:
        parts.append(f"in {yr.group(1)}")
    grp = re.search(r"group by ([a-z_.]+)", s)
    if grp:
        parts.append(f"grouped by {grp.group(1).split('.')[-1]}")
    if "order_id is null" in s:
        parts.append("with no orders")
    return " ".join(parts) + "."


if __name__ == "__main__":  # self-check
    assert "GROUP BY c.name" in question_to_sql("what is the revenue by category")[0]
    assert question_to_sql("show me the weather")[0] == ""  # unanswerable
    assert "COUNT(*)" in question_to_sql("how many customers do we have")[0]
    assert "ORDER BY price DESC" in question_to_sql("what is the most expensive product")[0]  # not 'top products'
    assert "customers" in question_to_sql("list all customers and their country")[0].lower()  # not a count of 'country'
    assert "ORDER BY revenue DESC" in question_to_sql("top 5 products by gross revenue")[0]
    d = describe_sql("SELECT COUNT(*) FROM orders WHERE status = 'completed'")
    assert "number of records" in d and "completed" in d, d
    print("stub_sql OK")
