"""PII redaction tests."""
from app.redaction import redact_rows, redact_text


def test_email_masked():
    assert redact_text("reach me at alice@example.com") == "reach me at a***@example.com"


def test_ssn_masked():
    assert redact_text("SSN 123-45-6789") == "SSN ***-**-6789"


def test_valid_card_masked_but_long_id_kept():
    masked = redact_text("pay 4111 1111 1111 1111")
    assert masked.endswith("1111") and "4111" not in masked
    assert redact_text("order 100000000000001") == "order 100000000000001"  # not Luhn -> untouched


def test_non_string_cells_untouched():
    assert redact_rows([["Widget", 5, 19.99]]) == [["Widget", 5, 19.99]]


def test_rows_redacted_cellwise():
    rows = [["bob@corp.io", "ok"], ["no pii here", "123-45-6789"]]
    assert redact_rows(rows) == [["b***@corp.io", "ok"], ["no pii here", "***-**-6789"]]
