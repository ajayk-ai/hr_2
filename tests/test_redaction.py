from __future__ import annotations

import pytest

from app.utils.redaction import mask_identifier, redact_text


@pytest.mark.parametrize(
    ("value", "visible", "expected"),
    [
        ("123412341234", 4, "XXXXXXXX1234"),
        ("1234 5678 9012", 4, "XXXXXXXX9012"),
        ("ABCDE1234F", 4, "XXXXXX234F"),
        ("123", 4, "123"),
        ("", 4, ""),
        (None, 4, ""),
    ],
)
def test_mask_identifier(value: str | None, visible: int, expected: str) -> None:
    assert mask_identifier(value, visible=visible) == expected


def test_redact_text_masks_aadhaar_and_pan() -> None:
    text = "Aadhaar 1234 5678 9012 and PAN ABCDE1234F belong to Ravi."
    redacted = redact_text(text)
    assert "9012" in redacted
    assert "1234 5678 9012" not in redacted
    assert "ABCDE1234F" not in redacted
    assert "Ravi" in redacted


def test_redact_text_masks_bank_account_numbers() -> None:
    assert "50100234567890" not in redact_text("A/C 50100234567890")


def test_redact_text_masks_email_local_part() -> None:
    assert redact_text("ravi.kumar@example.com").endswith("@example.com")
    assert "ravi.kumar" not in redact_text("ravi.kumar@example.com")


def test_redact_text_is_a_noop_on_clean_text() -> None:
    assert redact_text("Salary slip for March 2024") == "Salary slip for March 2024"
