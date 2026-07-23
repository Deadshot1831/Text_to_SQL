"""Phase 4.3: in-session history + the feedback flywheel.

History lives in memory (per process). Feedback is also appended to a JSONL file
so it survives restarts and can be harvested: incorrect answers become new eval
cases, correct ones become new few-shot examples.

ponytail: in-memory deque + JSONL, not a database. Swap `_history` for a table
if history needs to outlive the process.
"""
from __future__ import annotations

import itertools
import json
import time
from collections import deque

from app.config import get_settings
from app.models import QueryResponse

_seq = itertools.count(1)
_history: deque[dict] = deque(maxlen=500)


def record_query(response: QueryResponse) -> int:
    qid = next(_seq)
    _history.append(
        {
            "id": qid,
            "ts": round(time.time(), 3),
            "question": response.question,
            "status": response.status,
            "sql": response.generated.sql if response.generated else None,
            "confidence": response.confidence.overall if response.confidence else None,
            "feedback": None,
        }
    )
    return qid


def history(limit: int = 50) -> list[dict]:
    return list(_history)[-limit:][::-1]


def record_feedback(query_id: int, correct: bool, note: str = "") -> dict:
    label = "correct" if correct else "incorrect"
    item = next((h for h in _history if h["id"] == query_id), None)
    if item is not None:
        item["feedback"] = label
    record = {
        "ts": round(time.time(), 3),
        "query_id": query_id,
        "label": label,
        "note": note,
        "question": item["question"] if item else None,
        "sql": item["sql"] if item else None,
    }
    try:
        with open(get_settings().feedback_log_path, "a") as f:
            f.write(json.dumps(record) + "\n")
    except OSError:
        pass
    return record
