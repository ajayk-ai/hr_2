"""Masking helpers for personally identifiable identifiers.

Two distinct jobs live here:

``redact_text``
    Best-effort scrubbing of free text headed for a log sink. It errs towards
    over-masking -- a masked non-identifier is harmless, a leaked Aadhaar is not.

``mask_identifier``
    Deterministic partial masking for values we deliberately surface (filenames,
    the JSON report), keeping enough characters for a human to disambiguate.
"""

from __future__ import annotations

import re

# 12 digits, optionally grouped in 4s -- the printed Aadhaar format.
_AADHAAR_RE = re.compile(r"\b\d{4}[\s-]?\d{4}[\s-]?\d{4}\b")
# Income Tax PAN: 5 letters, 4 digits, 1 letter.
_PAN_RE = re.compile(r"\b[A-Z]{5}\d{4}[A-Z]\b")
# Bank account / long numeric runs (9-18 digits) not already caught above.
_LONG_DIGITS_RE = re.compile(r"\b\d{9,18}\b")
_EMAIL_RE = re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.-]+\b")


def mask_identifier(value: str | None, *, visible: int = 4) -> str:
    """Mask all but the last ``visible`` alphanumeric characters.

    >>> mask_identifier("123412341234")
    'XXXXXXXX1234'
    >>> mask_identifier("ABCDE1234F", visible=4)
    'XXXXXX234F'
    """
    if not value:
        return ""
    cleaned = re.sub(r"[\s-]", "", value)
    if len(cleaned) <= visible:
        return cleaned
    return "X" * (len(cleaned) - visible) + cleaned[-visible:]


def redact_text(text: str) -> str:
    """Mask identifiers found anywhere in ``text``. Safe for logs."""
    if not text:
        return text
    text = _AADHAAR_RE.sub(lambda m: mask_identifier(m.group()), text)
    text = _PAN_RE.sub(lambda m: mask_identifier(m.group()), text)
    text = _LONG_DIGITS_RE.sub(lambda m: mask_identifier(m.group()), text)
    text = _EMAIL_RE.sub(_mask_email, text)
    return text


def _mask_email(match: re.Match[str]) -> str:
    local, _, domain = match.group().partition("@")
    head = local[0] if local else ""
    return f"{head}{'X' * max(len(local) - 1, 1)}@{domain}"
