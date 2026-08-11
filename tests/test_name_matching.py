"""Name compatibility across a candidate's documents.

Two failure modes pull against each other. Too strict and HR drowns in alarms,
because Indian documents abbreviate inconsistently and legitimately. Too lax and
another person's document sits in the joining file unnoticed. These tests pin
both edges.
"""

from __future__ import annotations

import pytest

from app.domain.naming import names_are_compatible


class TestCompatible:
    @pytest.mark.parametrize(
        ("left", "right"),
        [
            ("AJAY KANAGARAJ", "AJAY K"),  # PAN abbreviates the surname
            ("AJAY K", "AJAY KANAGARAJ"),  # order must not matter
            ("Ravi Kumar Sharma", "Ravi Sharma"),  # middle name dropped on a resume
            ("RAVI KUMAR SHARMA", "ravi kumar sharma"),  # case
            ("Ravi  Kumar", "Ravi Kumar"),  # spacing
            ("Ravi Kumar", "Kumar Ravi"),  # order varies between documents
            ("R K Sharma", "Ravi Kumar Sharma"),  # initials throughout
            ("Ravi Kumár", "Ravi Kumar"),  # diacritic from OCR
            ("Ravi Kumar", ""),  # no evidence is not a mismatch
            ("", "Ravi Kumar"),
        ],
    )
    def test_legitimate_variants_are_accepted(self, left: str, right: str) -> None:
        assert names_are_compatible(left, right) is True


class TestIncompatible:
    @pytest.mark.parametrize(
        ("left", "right"),
        [
            ("Ajay Kanagaraj", "Priya Sharma"),  # a different person entirely
            ("Ravi Kumar", "Ravi Prakash"),  # shared given name, different surname
            ("Ravi Kumar Sharma", "Suresh Kumar Sharma"),  # differing given name
        ],
    )
    def test_genuinely_different_names_are_rejected(self, left: str, right: str) -> None:
        assert names_are_compatible(left, right) is False

    def test_an_initial_does_not_match_an_unrelated_surname(self) -> None:
        """`K` may stand for Kanagaraj. It must not stand for Sharma."""
        assert names_are_compatible("Ajay K", "Ajay Sharma") is False

    def test_each_token_is_consumed_only_once(self) -> None:
        """Without this, "Ravi Ravi" would match any name containing one "Ravi"."""
        assert names_are_compatible("Ravi Ravi", "Ravi Kumar") is False
