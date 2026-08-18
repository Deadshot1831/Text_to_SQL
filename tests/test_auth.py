"""Auth tests: registration, login, token gating, password hashing."""
import secrets

from fastapi.testclient import TestClient

from app import auth
from app.main import app

client = TestClient(app)
auth.init_auth_db()


def _user() -> str:
    return "u" + secrets.token_hex(4)


def test_register_returns_working_token():
    u, pw = _user(), "secret12345"
    r = client.post("/v1/auth/register", json={"username": u, "password": pw})
    assert r.status_code == 200
    tok = r.json()["access_token"]
    me = client.get("/v1/auth/me", headers={"Authorization": f"Bearer {tok}"})
    assert me.status_code == 200 and me.json()["username"] == u


def test_login_gates_protected_endpoint():
    u, pw = _user(), "secret12345"
    client.post("/v1/auth/register", json={"username": u, "password": pw})
    tok = client.post("/v1/auth/login", json={"username": u, "password": pw}).json()["access_token"]
    h = {"Authorization": f"Bearer {tok}"}
    assert client.get("/v1/schema", headers=h).status_code == 200
    assert client.get("/v1/schema").status_code == 401  # no token -> blocked


def test_wrong_password_rejected():
    u = _user()
    client.post("/v1/auth/register", json={"username": u, "password": "rightpass123"})
    r = client.post("/v1/auth/login", json={"username": u, "password": "wrongpass123"})
    assert r.status_code == 401


def test_duplicate_username_conflicts():
    u = _user()
    assert client.post("/v1/auth/register", json={"username": u, "password": "pass123456"}).status_code == 200
    assert client.post("/v1/auth/register", json={"username": u, "password": "pass123456"}).status_code == 409


def test_malformed_token_rejected():
    assert client.get("/v1/auth/me", headers={"Authorization": "Bearer not.a.real.token"}).status_code == 401


def test_logout_revokes_access_token():
    u = _user()
    r = client.post("/v1/auth/register", json={"username": u, "password": "Val1dphrase"}).json()
    h = {"Authorization": f"Bearer {r['access_token']}"}
    assert client.get("/v1/auth/me", headers=h).status_code == 200
    assert client.post("/v1/auth/logout", headers=h).status_code == 200
    assert client.get("/v1/auth/me", headers=h).status_code == 401  # revoked, not just expired


def test_refresh_rotates_and_issues_new_access():
    u = _user()
    r = client.post("/v1/auth/register", json={"username": u, "password": "Val1dphrase"}).json()
    refresh_tok = r["refresh_token"]
    assert refresh_tok
    r2 = client.post("/v1/auth/refresh", json={"refresh_token": refresh_tok})
    assert r2.status_code == 200
    new_access = r2.json()["access_token"]
    assert client.get("/v1/auth/me", headers={"Authorization": f"Bearer {new_access}"}).status_code == 200
    # the old refresh token was rotated -> cannot be reused
    assert client.post("/v1/auth/refresh", json={"refresh_token": refresh_tok}).status_code == 401


def test_refresh_token_is_not_accepted_as_access():
    u = _user()
    r = client.post("/v1/auth/register", json={"username": u, "password": "Val1dphrase"}).json()
    # a refresh token must not authenticate protected endpoints
    assert client.get("/v1/auth/me", headers={"Authorization": f"Bearer {r['refresh_token']}"}).status_code == 401


def test_password_is_hashed_not_stored_plaintext():
    h = auth.hash_password("hunter2pw")
    assert h != "hunter2pw" and h.startswith("$2")
    assert auth.verify_password("hunter2pw", h)
    assert not auth.verify_password("wrong", h)


def test_weak_passwords_rejected():
    u = _user()
    assert client.post("/v1/auth/register", json={"username": u, "password": "password"}).status_code == 400
    assert client.post("/v1/auth/register", json={"username": u, "password": u + "extra"}).status_code == 400  # contains username
    assert client.post("/v1/auth/register", json={"username": u, "password": "aaaaaaaa"}).status_code == 400  # not varied


def test_strong_password_accepted():
    u = _user()
    assert client.post("/v1/auth/register", json={"username": u, "password": "Str0ng!phrase"}).status_code == 200


def test_secret_policy_helper():
    assert auth._secret_ok("development", "")           # dev: anything goes
    assert not auth._secret_ok("production", "")        # prod: empty rejected
    assert not auth._secret_ok("production", "tooshort")
    assert auth._secret_ok("production", "x" * 32)      # prod: long secret ok
