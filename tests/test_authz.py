"""Per-user table authorization (RBAC) tests."""
import secrets

from fastapi.testclient import TestClient

from app import auth
from app.authz import forbidden_tables
from app.main import app

client = TestClient(app)
auth.init_auth_db()


def test_forbidden_tables_helper():
    assert forbidden_tables("SELECT * FROM customers", None) == []          # unrestricted
    assert forbidden_tables("SELECT * FROM products", {"products"}) == []
    assert forbidden_tables("SELECT * FROM customers", {"products"}) == ["customers"]


def _login(username: str, password: str = "Val1dphrase") -> dict:
    tok = client.post("/v1/auth/login", json={"username": username, "password": password}).json()["access_token"]
    return {"Authorization": f"Bearer {tok}"}


def test_restricted_user_blocked_from_forbidden_table():
    u = "rbac_" + secrets.token_hex(3)
    auth.create_user(u, "Val1dphrase", allowed_tables=["products", "categories"])
    r = client.post("/v1/query",
                    json={"question": "all customers", "sql_override": "SELECT * FROM customers"},
                    headers=_login(u))
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "blocked"
    assert "not authorized" in (body["error"] or "")


def test_restricted_user_allowed_table_runs():
    u = "rbac_" + secrets.token_hex(3)
    auth.create_user(u, "Val1dphrase", allowed_tables=["products", "categories"])
    r = client.post("/v1/query",
                    json={"question": "list products", "sql_override": "SELECT name FROM products"},
                    headers=_login(u))
    assert r.json()["status"] == "ok"


def test_unrestricted_user_can_query_any_table():
    u = "free_" + secrets.token_hex(3)
    auth.create_user(u, "Val1dphrase")  # no allow-list -> unrestricted
    r = client.post("/v1/query",
                    json={"question": "customers", "sql_override": "SELECT * FROM customers"},
                    headers=_login(u))
    assert r.json()["status"] == "ok"
