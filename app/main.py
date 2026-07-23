"""Phase 4.1: FastAPI service.

  POST /v1/query     natural-language question -> SQL + results + confidence
  POST /v1/feedback  mark a past result correct/incorrect (the flywheel)
  GET  /v1/schema    the introspected database schema
  GET  /v1/history   past queries for this session
"""
from __future__ import annotations

from fastapi import FastAPI
from pydantic import BaseModel

from app import store
from app.config import get_settings
from app.models import QueryRequest, QueryResponse
from app.pipeline import answer, get_snapshot

app = FastAPI(
    title="Text-to-SQL with Guardrails and Hallucination Detection",
    version="1.0",
)


@app.get("/")
def root() -> dict:
    s = get_settings()
    return {
        "service": "text-to-sql",
        "status": "ok",
        "llm_provider": s.effective_provider,
        "model": s.llm_model,
    }


@app.post("/v1/query", response_model=QueryResponse)
def query(req: QueryRequest) -> QueryResponse:
    resp = answer(req)
    resp.query_id = store.record_query(resp)
    return resp


class FeedbackIn(BaseModel):
    query_id: int
    correct: bool
    note: str = ""


@app.post("/v1/feedback")
def feedback(fb: FeedbackIn) -> dict:
    return store.record_feedback(fb.query_id, fb.correct, fb.note)


@app.get("/v1/schema")
def schema() -> dict:
    return get_snapshot().to_dict()


@app.get("/v1/history")
def history(limit: int = 50) -> list[dict]:
    return store.history(limit)
