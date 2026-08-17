"""Best-effort PII redaction for query results and logged text.

Masks emails, US SSNs, and Luhn-valid card numbers in string values. Numeric
cells are left alone; a Luhn check keeps ordinary long IDs from being mistaken
for card numbers.

ponytail: regex + Luhn, not a full PII classifier. Add named-entity detection if
you need names/addresses too; the call sites (redact_rows / redact_text) stay put.
"""
from __future__ import annotations

import re

_EMAIL = re.compile(r"([A-Za-z0-9._%+\-]+)@([A-Za-z0-9.\-]+\.[A-Za-z]{2,})")
_SSN = re.compile(r"\b\d{3}-\d{2}-(\d{4})\b")
_DIGIT_RUN = re.compile(r"\b(?:\d[ -]?){13,19}\b")


def _luhn_ok(digits: str) -> bool:
    ds = [int(c) for c in digits if c.isdigit()]
    if len(ds) < 13:
        return False
    total, parity = 0, len(ds) % 2
    for i, d in enumerate(ds):
        if i % 2 == parity:
            d *= 2
            if d > 9:
                d -= 9
        total += d
    return total % 10 == 0


def _mask_email(m: re.Match) -> str:
    local = m.group(1)
    return (local[0] + "***" if local else "***") + "@" + m.group(2)


def _mask_card(m: re.Match) -> str:
    raw = m.group(0)
    digits = "".join(c for c in raw if c.isdigit())
    if not _luhn_ok(digits):
        return raw  # not a card number — leave it (e.g. a long order id)
    return "•••• •••• •••• " + digits[-4:]


def redact_text(value):
    """Redact PII in a string; non-strings pass through unchanged."""
    if not isinstance(value, str):
        return value
    v = _EMAIL.sub(_mask_email, value)
    v = _SSN.sub(r"***-**-\1", v)
    v = _DIGIT_RUN.sub(_mask_card, v)
    return v


def redact_rows(rows: list[list]) -> list[list]:
    return [[redact_text(cell) for cell in row] for row in rows]


if __name__ == "__main__":
    assert redact_text("contact a@b.com") == "contact a***@b.com"
    assert redact_text("ssn 123-45-6789") == "ssn ***-**-6789"
    assert redact_text("card 4111 1111 1111 1111").endswith("1111") and "4111" not in redact_text("card 4111 1111 1111 1111")
    assert redact_text("order 100000000000001") == "order 100000000000001"  # not Luhn-valid -> kept
    assert redact_rows([["Mechanical Keyboard", 5]]) == [["Mechanical Keyboard", 5]]
    print("redaction self-check OK")
