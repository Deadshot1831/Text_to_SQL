"""LLM provider abstraction: anthropic (default), openai, or offline stub.

Everything upstream calls `complete(system, user)`. When no API key is set for
the configured provider, `effective_provider` falls back to the deterministic
stub so the full pipeline still runs offline (tests, evals, demo).
"""
from __future__ import annotations

import json

from app.config import get_settings


class LLMError(RuntimeError):
    pass


def complete(system: str, user: str, max_tokens: int = 1024) -> str:
    provider = get_settings().effective_provider
    if provider == "anthropic":
        return _anthropic(system, user, max_tokens)
    if provider == "openai":
        return _openai(system, user, max_tokens)
    from app.stub_sql import stub_response

    return stub_response(system, user)


def _anthropic(system: str, user: str, max_tokens: int) -> str:
    import anthropic

    s = get_settings()
    client = anthropic.Anthropic(api_key=s.anthropic_api_key)
    msg = client.messages.create(
        model=s.llm_model,
        max_tokens=max_tokens,
        system=system,
        messages=[{"role": "user", "content": user}],
    )
    return "".join(b.text for b in msg.content if getattr(b, "type", None) == "text")


def _openai(system: str, user: str, max_tokens: int) -> str:
    import openai

    s = get_settings()
    client = openai.OpenAI(api_key=s.openai_api_key)
    resp = client.chat.completions.create(
        model=s.llm_model,
        max_tokens=max_tokens,
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
    )
    return resp.choices[0].message.content or ""


def parse_json_block(text: str) -> dict:
    """Extract the first balanced {...} JSON object from an LLM response.

    Tolerates code fences and surrounding prose that models sometimes add.
    """
    start = text.find("{")
    if start == -1:
        raise LLMError(f"no JSON object in response: {text[:200]!r}")
    depth = 0
    in_str = False
    escape = False
    for i in range(start, len(text)):
        ch = text[i]
        if in_str:
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == '"':
                in_str = False
        else:
            if ch == '"':
                in_str = True
            elif ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    return json.loads(text[start : i + 1])
    raise LLMError("unbalanced JSON object in response")


if __name__ == "__main__":  # self-check
    assert parse_json_block('prefix {"a": 1, "b": "x}y"} suffix') == {"a": 1, "b": "x}y"}
    assert parse_json_block('```json\n{"sql": "SELECT 1"}\n```')["sql"] == "SELECT 1"
    print("llm parse OK")
