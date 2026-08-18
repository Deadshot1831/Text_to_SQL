"""Per-user table authorization (RBAC) for generated SQL.

A user may carry an allow-list of tables; any query touching a table outside it is
refused *before execution*. `allowed is None` means unrestricted.
"""
from __future__ import annotations

from app.generate import referenced_tables


def forbidden_tables(sql: str, allowed: set[str] | None) -> list[str]:
    """Tables the SQL references that the user is not allowed to query (empty = permitted)."""
    if allowed is None:  # unrestricted
        return []
    refs = {t.lower() for t in referenced_tables(sql)}
    allow = {t.lower() for t in allowed}
    return sorted(refs - allow)


if __name__ == "__main__":
    assert forbidden_tables("SELECT * FROM customers", None) == []  # unrestricted
    assert forbidden_tables("SELECT * FROM products", {"products", "categories"}) == []
    assert forbidden_tables("SELECT * FROM customers c JOIN orders o ON 1", {"products"}) == ["customers", "orders"]
    print("authz self-check OK")
