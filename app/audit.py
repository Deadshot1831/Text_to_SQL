"""Append-only JSONL audit log. Every blocked query and execution is recorded."""
from __future__ import annotations

import json
import time

from app.config import get_settings


def log_event(event: str, **fields) -> dict:
    record = {"ts": round(time.time(), 3), "event": event, **fields}
    try:
        with open(get_settings().audit_log_path, "a") as f:
            f.write(json.dumps(record, default=str) + "\n")
    except OSError:
        pass  # never let auditing break a request
    return record
