"""Phase 4.1: FastAPI service.

  POST /v1/auth/register  create an account -> access token
  POST /v1/auth/login     username + password -> access token
  GET  /v1/auth/me        the current user (requires token)
  POST /v1/query     natural-language question -> SQL + results + confidence
  POST /v1/feedback  mark a past result correct/incorrect (the flywheel)
  GET  /v1/schema    the introspected database schema
  GET  /v1/history   past queries for this session

Everything under /v1 except the auth endpoints requires a Bearer token.
"""
from __future__ import annotations

import secrets as _secrets
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field
from starlette.middleware.base import BaseHTTPMiddleware

from app import auth, store
from app.config import get_settings
from app.models import QueryRequest, QueryResponse
from app.pipeline import answer, get_snapshot
from app.ratelimit import FailureLimiter

_login_limiter = FailureLimiter(
    get_settings().auth_max_failures, get_settings().auth_failure_window_seconds
)


def _throttle_key(username: str, request: Request) -> str:
    ip = request.client.host if request.client else "?"
    return f"{(username or '').lower()}|{ip}"


@asynccontextmanager
async def lifespan(app: FastAPI):
    auth.init_auth_db()
    yield


app = FastAPI(
    title="Text-to-SQL with Guardrails and Hallucination Detection",
    version="1.0",
    lifespan=lifespan,
)


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """Add defensive response headers on every request."""

    async def dispatch(self, request: Request, call_next):
        resp = await call_next(request)
        h = resp.headers
        h.setdefault("X-Content-Type-Options", "nosniff")
        h.setdefault("X-Frame-Options", "DENY")
        h.setdefault("Referrer-Policy", "no-referrer")
        h.setdefault("Permissions-Policy", "geolocation=(), microphone=(), camera=()")
        if get_settings().hsts_enabled and request.url.scheme == "https":
            h.setdefault("Strict-Transport-Security", "max-age=31536000; includeSubDomains")
        return resp


def configure_cors(app: FastAPI, origins: list[str]) -> None:
    """Allow cross-origin calls from `origins`. Auth is Bearer tokens (no cookies),
    so credentials stay off. With no origins configured the API is same-origin only."""
    if origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=origins,
            allow_methods=["*"],
            allow_headers=["*"],
        )


configure_cors(app, get_settings().cors_origins)
if get_settings().security_headers_enabled:
    app.add_middleware(SecurityHeadersMiddleware)


@app.get("/")
def root() -> dict:
    s = get_settings()
    return {
        "service": "text-to-sql",
        "status": "ok",
        "llm_provider": s.effective_provider,
        "model": s.llm_model,
    }


_LOGIN_PAGE = Path(__file__).resolve().parent.parent / "docs" / "login.html"


@app.get("/login", include_in_schema=False)
def login_page() -> HTMLResponse:
    """The sign-in / sign-up landing page (served same-origin so its fetch calls work).

    A per-request nonce is injected into the page's inline <style>/<script> so we can
    ship a strict Content-Security-Policy with no 'unsafe-inline'.
    """
    html = _LOGIN_PAGE.read_text(encoding="utf-8")
    nonce = _secrets.token_urlsafe(16)
    html = html.replace("<style>", f'<style nonce="{nonce}">', 1)
    html = html.replace("<script>", f'<script nonce="{nonce}">', 1)
    csp = (
        "default-src 'none'; base-uri 'none'; frame-ancestors 'none'; form-action 'self'; "
        f"style-src 'nonce-{nonce}'; script-src 'nonce-{nonce}'; "
        "connect-src 'self'; img-src 'self' data:"
    )
    return HTMLResponse(html, headers={"Content-Security-Policy": csp})


# ---------------- auth ----------------
class RegisterIn(BaseModel):
    username: str = Field(min_length=3, max_length=64)
    password: str = Field(min_length=8, max_length=128)


class LoginIn(BaseModel):
    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=128)


class TokenOut(BaseModel):
    access_token: str
    token_type: str = "bearer"
    username: str
    refresh_token: str | None = None


class RefreshIn(BaseModel):
    refresh_token: str


class LogoutIn(BaseModel):
    refresh_token: str | None = None


def _tokens_for(user: str) -> TokenOut:
    return TokenOut(
        access_token=auth.create_access_token(user),
        refresh_token=auth.create_refresh_token(user),
        username=user,
    )


@app.post("/v1/auth/register", response_model=TokenOut)
def register(body: RegisterIn) -> TokenOut:
    try:
        auth.validate_password_strength(body.username, body.password)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    try:
        auth.create_user(body.username, body.password)
    except ValueError as e:
        raise HTTPException(status_code=409, detail=str(e))
    return _tokens_for(body.username)


@app.post("/v1/auth/login", response_model=TokenOut)
def login(body: LoginIn, request: Request) -> TokenOut:
    key = _throttle_key(body.username, request)
    locked, retry = _login_limiter.is_locked(key)
    if locked:
        raise HTTPException(
            status_code=429,
            detail="too many failed attempts; try again later",
            headers={"Retry-After": str(retry)},
        )
    user = auth.authenticate(body.username, body.password)
    if not user:
        _login_limiter.register_failure(key)
        raise HTTPException(status_code=401, detail="invalid username or password")
    _login_limiter.reset(key)
    return _tokens_for(user)


@app.post("/v1/auth/refresh", response_model=TokenOut)
def refresh(body: RefreshIn) -> TokenOut:
    payload = auth.decode(body.refresh_token)
    if not payload or payload.get("type") != "refresh" or auth.is_revoked(payload.get("jti", "")):
        raise HTTPException(status_code=401, detail="invalid or expired refresh token")
    auth.revoke_payload(payload)  # rotate: the old refresh token can't be reused
    return _tokens_for(payload["sub"])


@app.post("/v1/auth/logout")
def logout(body: LogoutIn | None = None, payload: dict = Depends(auth.get_current_payload)) -> dict:
    auth.revoke_payload(payload)  # invalidate this access token server-side
    if body and body.refresh_token:
        rp = auth.decode(body.refresh_token)
        if rp and rp.get("type") == "refresh":
            auth.revoke_payload(rp)
    return {"status": "logged out"}


@app.get("/v1/auth/me")
def me(user: str = Depends(auth.get_current_user)) -> dict:
    return {"username": user}


# ---------------- protected app ----------------
@app.post("/v1/query", response_model=QueryResponse)
def query(req: QueryRequest, user: str = Depends(auth.get_current_user)) -> QueryResponse:
    resp = answer(req, allowed_tables=auth.user_allowed_tables(user))
    resp.query_id = store.record_query(resp)
    return resp


class FeedbackIn(BaseModel):
    query_id: int
    correct: bool
    note: str = ""


@app.post("/v1/feedback")
def feedback(fb: FeedbackIn, user: str = Depends(auth.get_current_user)) -> dict:
    return store.record_feedback(fb.query_id, fb.correct, fb.note)


@app.get("/v1/schema")
def schema(user: str = Depends(auth.get_current_user)) -> dict:
    return get_snapshot().to_dict()


@app.get("/v1/history")
def history(limit: int = 50, user: str = Depends(auth.get_current_user)) -> list[dict]:
    return store.history(limit)
