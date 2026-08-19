"""Admin user-management endpoint tests."""
import secrets

from fastapi.testclient import TestClient

from app import auth
from app.main import app

client = TestClient(app)
auth.init_auth_db()


def _token(username: str, password: str) -> str:
    return client.post("/v1/auth/login", json={"username": username, "password": password}).json()["access_token"]


def _admin_headers() -> dict:
    return {"Authorization": f"Bearer {_token('demo', 'demo12345')}"}


def test_non_admin_is_forbidden():
    u = "na_" + secrets.token_hex(3)
    client.post("/v1/auth/register", json={"username": u, "password": "Val1dphrase"})
    h = {"Authorization": f"Bearer {_token(u, 'Val1dphrase')}"}
    assert client.get("/v1/admin/users", headers=h).status_code == 403
    assert client.patch(f"/v1/admin/users/{u}", json={"is_admin": True}, headers=h).status_code == 403


def test_admin_can_list_users():
    r = client.get("/v1/admin/users", headers=_admin_headers())
    assert r.status_code == 200
    demo = next(x for x in r.json() if x["username"] == "demo")
    assert demo["is_admin"] is True


def test_admin_sets_and_lifts_table_access():
    target = "tgt_" + secrets.token_hex(3)
    client.post("/v1/auth/register", json={"username": target, "password": "Val1dphrase"})
    th = {"Authorization": f"Bearer {_token(target, 'Val1dphrase')}"}

    # restrict -> customers become off-limits
    r = client.patch(f"/v1/admin/users/{target}",
                     json={"allowed_tables": ["products", "categories"]}, headers=_admin_headers())
    assert r.status_code == 200 and r.json()["allowed_tables"] == ["products", "categories"]
    blocked = client.post("/v1/query",
                          json={"question": "x", "sql_override": "SELECT * FROM customers"}, headers=th)
    assert blocked.json()["status"] == "blocked"

    # lift restriction (explicit null) -> unrestricted again
    r2 = client.patch(f"/v1/admin/users/{target}", json={"allowed_tables": None}, headers=_admin_headers())
    assert r2.json()["allowed_tables"] is None
    ok = client.post("/v1/query",
                     json={"question": "x", "sql_override": "SELECT * FROM customers"}, headers=th)
    assert ok.json()["status"] == "ok"


def test_admin_update_unknown_user_404():
    r = client.patch("/v1/admin/users/nobody_here", json={"is_admin": True}, headers=_admin_headers())
    assert r.status_code == 404
