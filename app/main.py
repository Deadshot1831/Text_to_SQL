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

from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from app import auth, store
from app.config import get_settings
from app.models import QueryRequest, QueryResponse
from app.pipeline import answer, get_snapshot


@asynccontextmanager
async def lifespan(app: FastAPI):
    auth.init_auth_db()
    yield


app = FastAPI(
    title="Text-to-SQL with Guardrails and Hallucination Detection",
    version="1.0",
    lifespan=lifespan,
)


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
def login_page() -> FileResponse:
    """The sign-in / sign-up landing page (served same-origin so its fetch calls work)."""
    return FileResponse(_LOGIN_PAGE)


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


@app.post("/v1/auth/register", response_model=TokenOut)
def register(body: RegisterIn) -> TokenOut:
    try:
        auth.create_user(body.username, body.password)
    except ValueError as e:
        raise HTTPException(status_code=409, detail=str(e))
    return TokenOut(access_token=auth.create_token(body.username), username=body.username)


@app.post("/v1/auth/login", response_model=TokenOut)
def login(body: LoginIn) -> TokenOut:
    user = auth.authenticate(body.username, body.password)
    if not user:
        raise HTTPException(status_code=401, detail="invalid username or password")
    return TokenOut(access_token=auth.create_token(user), username=user)


@app.get("/v1/auth/me")
def me(user: str = Depends(auth.get_current_user)) -> dict:
    return {"username": user}


# ---------------- protected app ----------------
@app.post("/v1/query", response_model=QueryResponse)
def query(req: QueryRequest, user: str = Depends(auth.get_current_user)) -> QueryResponse:
    resp = answer(req)
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
