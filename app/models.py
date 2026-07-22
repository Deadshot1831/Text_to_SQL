"""Shared Pydantic models for the request/response contract."""
from __future__ import annotations

from pydantic import BaseModel, Field


# ---- Ambiguity / clarification (Phase 1.4) ----
class Interpretation(BaseModel):
    label: str
    description: str
    example_sql: str


class Clarification(BaseModel):
    term: str
    question: str
    interpretations: list[Interpretation]


# ---- Generation (Phase 2.1) ----
class GeneratedSQL(BaseModel):
    sql: str
    explanation: str
    confidence: float = Field(ge=0.0, le=1.0)  # model self-reported
    tables: list[str] = []
    columns: list[str] = []


# ---- Guardrails (Phase 2.2) ----
class GuardrailResult(BaseModel):
    allowed: bool
    violations: list[str] = []
    warnings: list[str] = []
    final_sql: str  # may differ from input (e.g. LIMIT injected)


# ---- Execution (Phase 2.4) ----
class ExecutionResult(BaseModel):
    columns: list[str] = []
    rows: list[list] = []
    row_count: int = 0
    truncated: bool = False
    execution_ms: float = 0.0
    explain_plan: str | None = None
    error: str | None = None


# ---- Validation / hallucination detection (Phase 3) ----
class SanityFlag(BaseModel):
    check: str
    severity: str  # "info" | "warn"
    message: str


class MultiQueryResult(BaseModel):
    alt_sql: str
    agree: bool
    note: str


class ValidationReport(BaseModel):
    backtranslation: str | None = None
    backtranslation_alignment: float = 0.0
    sanity_flags: list[SanityFlag] = []
    multiquery: MultiQueryResult | None = None


class ConfidenceBreakdown(BaseModel):
    syntax_valid: float = 0.0
    backtranslation_alignment: float = 0.0
    sanity_pass_rate: float = 0.0
    multiquery_agreement: float | None = None
    schema_coverage: float = 0.0
    overall: float = 0.0


# ---- API contract ----
class QueryRequest(BaseModel):
    question: str
    row_limit: int | None = None
    multi_query: bool = False  # run the independent second-query check (Phase 3.3)


class QueryResponse(BaseModel):
    question: str
    status: str  # ok | clarification_needed | blocked | error
    clarification: Clarification | None = None
    generated: GeneratedSQL | None = None
    guardrail: GuardrailResult | None = None
    execution: ExecutionResult | None = None
    validation: ValidationReport | None = None
    confidence: ConfidenceBreakdown | None = None
    warnings: list[str] = []
    error: str | None = None
