"""API smoke tests via FastAPI TestClient (stub provider)."""
from fastapi.testclient import TestClient

from app import auth
from app.main import app

client = TestClient(app)

# All /v1 app endpoints require a token now — authenticate this client once.
auth.init_auth_db()
try:
    auth.create_user("tester", "testerpw123")
except ValueError:
    pass  # already created by a previous run
_token = client.post("/v1/auth/login", json={"username": "tester", "password": "testerpw123"}).json()["access_token"]
client.headers.update({"Authorization": f"Bearer {_token}"})


def test_cors_configured_origin_is_allowed():
    # Tests the wiring directly so it doesn't depend on env at import time.
    from fastapi import FastAPI

    from app.main import configure_cors

    a = FastAPI()

    @a.get("/ping")
    def ping():
        return {"ok": True}

    configure_cors(a, ["http://example.com"])
    c = TestClient(a)
    r = c.get("/ping", headers={"Origin": "http://example.com"})
    assert r.headers.get("access-control-allow-origin") == "http://example.com"
    # an un-listed origin gets no allow header
    r2 = c.get("/ping", headers={"Origin": "http://evil.com"})
    assert r2.headers.get("access-control-allow-origin") in (None, "")


def test_login_page_served_publicly():
    anon = TestClient(app)  # the landing page must be reachable without a token
    r = anon.get("/login")
    assert r.status_code == 200
    assert "text/html" in r.headers["content-type"]
    assert "Sign in" in r.text and "Create account" in r.text


def test_protected_endpoints_require_auth():
    anon = TestClient(app)  # no Authorization header
    assert anon.post("/v1/query", json={"question": "x"}).status_code == 401
    assert anon.get("/v1/schema").status_code == 401
    assert anon.get("/v1/history").status_code == 401


def test_query_endpoint_ok():
    r = client.post("/v1/query", json={"question": "how many customers do we have?"})
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    assert body["confidence"]["overall"] > 0.8
    assert body["query_id"]


def test_query_endpoint_blocked_via_override():
    r = client.post("/v1/query", json={"question": "wipe it", "sql_override": "DROP TABLE orders"})
    assert r.json()["status"] == "blocked"


def test_schema_endpoint():
    r = client.get("/v1/schema")
    assert r.status_code == 200
    names = [t["name"] for t in r.json()["tables"]]
    assert {"orders", "customers", "products"} <= set(names)


def test_feedback_and_history_roundtrip():
    q = client.post("/v1/query", json={"question": "list all products"}).json()
    fb = client.post("/v1/feedback", json={"query_id": q["query_id"], "correct": True}).json()
    assert fb["label"] == "correct"
    hist = client.get("/v1/history").json()
    entry = next(h for h in hist if h["id"] == q["query_id"])
    assert entry["feedback"] == "correct"
