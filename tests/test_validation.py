"""Identifier validation and OCR repair.

No real Aadhaar or PAN numbers appear here. Aadhaar test values are *generated*
from the checksum algorithm, which is both safer and a stronger test: a hardcoded
number proves one case, while round-tripping generated ones proves the algorithm.
"""

from __future__ import annotations

import pytest

from app.domain.validation import (
    validate_aadhaar,
    validate_ifsc,
    validate_pan,
    verhoeff_check_digit,
    verhoeff_checksum,
)


def make_aadhaar(prefix: str) -> str:
    """Complete an 11-digit prefix into a checksum-valid 12-digit number."""
    assert len(prefix) == 11
    return prefix + str(verhoeff_check_digit(prefix))


class TestVerhoeff:
    def test_generated_numbers_validate(self) -> None:
        for prefix in ("23456789012", "98765432101", "45678901234"):
            assert verhoeff_checksum(make_aadhaar(prefix)) == 0

    def test_it_catches_every_single_digit_error(self) -> None:
        """The property Verhoeff exists for, and the one OCR needs most."""
        number = make_aadhaar("23456789012")
        for position in range(len(number)):
            for replacement in "0123456789":
                if replacement == number[position]:
                    continue
                corrupted = number[:position] + replacement + number[position + 1 :]
                assert verhoeff_checksum(corrupted) != 0, f"missed {number} -> {corrupted}"

    def test_it_catches_adjacent_transpositions(self) -> None:
        """A checksum that misses these is barely better than a length check."""
        number = make_aadhaar("23456789012")
        for i in range(len(number) - 1):
            if number[i] == number[i + 1]:
                continue
            swapped = number[:i] + number[i + 1] + number[i] + number[i + 2 :]
            assert verhoeff_checksum(swapped) != 0, f"missed transposition at {i}"


class TestPan:
    def test_a_clean_pan_passes_untouched(self) -> None:
        result = validate_pan("ABCDE1234F")

        assert result.usable is True
        assert result.value == "ABCDE1234F"
        assert result.repaired is False

    @pytest.mark.parametrize(
        ("misread", "expected"),
        [
            ("ABCDEI234F", "ABCDE1234F"),  # I read for 1 in a digit position
            ("ABCDE1O34F", "ABCDE1034F"),  # O read for 0
            ("ABCDES234F", "ABCDE5234F"),  # S read for 5
            ("0BCDE1234F", "OBCDE1234F"),  # 0 read for O in a letter position
            ("ABCDE1234的", "ABCDE1234"),  # non-ASCII stripped, then too short
        ],
    )
    def test_positional_repair(self, misread: str, expected: str) -> None:
        result = validate_pan(misread)

        assert result.value == expected

    @pytest.mark.parametrize("weak", ["4BCDE1234F", "3BCDE1234F", "7BCDE1234F"])
    def test_weak_glyph_confusions_are_rejected_rather_than_guessed(self, weak: str) -> None:
        """The format proves the character is a letter, not *which* letter.

        `0`/`O` and `1`/`I` are near-identical glyphs, so repairing them is a
        correction. `4`/`A` is a resemblance, and picking `A` would be a guess
        that produces a plausible, unverifiable, wrong PAN. Rejection sends the
        document to review, which is recoverable; a silently wrong identifier is
        not.
        """
        assert validate_pan(weak).usable is False

    def test_repair_is_flagged_not_silent(self) -> None:
        """HR must be able to see that the system altered what OCR read."""
        result = validate_pan("ABCDEI234F")

        assert result.usable is True
        assert result.repaired is True
        assert "ABCDEI234F" in result.note and "ABCDE1234F" in result.note

    def test_punctuation_and_spacing_are_tolerated(self) -> None:
        assert validate_pan("abcde 1234-f").value == "ABCDE1234F"

    @pytest.mark.parametrize("bad", ["", "ABCDE123", "ABCDE12345F", "1234567890"])
    def test_unrecoverable_values_are_rejected(self, bad: str) -> None:
        assert validate_pan(bad).usable is False

    def test_an_all_digit_string_is_not_coerced_into_a_pan(self) -> None:
        """Repair must not manufacture a valid-looking PAN from a phone number."""
        result = validate_pan("9876543210")

        assert result.usable is False


class TestAadhaar:
    def test_a_generated_number_passes(self) -> None:
        result = validate_aadhaar(make_aadhaar("23456789012"))

        assert result.usable is True
        assert result.repaired is False

    def test_spacing_in_the_printed_form_is_tolerated(self) -> None:
        number = make_aadhaar("23456789012")
        spaced = f"{number[:4]} {number[4:8]} {number[8:]}"

        assert validate_aadhaar(spaced).usable is True

    def test_letters_are_repaired_to_digits(self) -> None:
        number = make_aadhaar("23456789012")
        misread = number.replace("1", "I", 1) if "1" in number else number

        result = validate_aadhaar(misread)

        assert result.value == number
        assert result.usable is True

    def test_a_plausible_but_invalid_number_is_rejected(self) -> None:
        """Twelve digits is not enough. This is the case a length check passes."""
        result = validate_aadhaar("123456789012")

        assert result.usable is False

    def test_numbers_starting_with_zero_or_one_are_rejected(self) -> None:
        assert validate_aadhaar("012345678901").usable is False
        assert validate_aadhaar("112345678901").usable is False

    def test_a_failed_checksum_says_so(self) -> None:
        number = make_aadhaar("23456789012")
        corrupted = number[:5] + ("7" if number[5] != "7" else "3") + number[6:]

        result = validate_aadhaar(corrupted)

        assert result.usable is False
        assert "checksum" in result.note.lower()

    def test_the_misread_value_survives_for_the_report(self) -> None:
        """HR needs the wrong value to locate the page in the scan."""
        result = validate_aadhaar("123456789012")

        assert result.value == "123456789012"


class TestIfsc:
    def test_a_clean_code_passes(self) -> None:
        result = validate_ifsc("HDFC0001234")

        assert result.usable is True
        assert result.value == "HDFC0001234"

    def test_the_reserved_fifth_character_is_repaired(self) -> None:
        """`O` for `0` in the one position RBI fixes -- the archetypal misread."""
        result = validate_ifsc("HDFCO001234")

        assert result.usable is True
        assert result.value == "HDFC0001234"
        assert result.repaired is True

    def test_digits_in_the_bank_code_are_repaired(self) -> None:
        assert validate_ifsc("HDF00001234").value == "HDFO0001234"

    @pytest.mark.parametrize("bad", ["", "HDFC000123", "HDFC00012345"])
    def test_wrong_lengths_are_rejected(self, bad: str) -> None:
        assert validate_ifsc(bad).usable is False
