"""Format validation and OCR repair for government identifiers.

OCR reads glyphs, not meaning. `ABCDE1234F` comes back as `ABCDEI234F` because
`1` and `I` are the same handful of pixels on a laser-printed PAN card, and the
model downstream has no reason to doubt it. Left alone that string becomes a
filename, and a filename nobody can search for is worse than no filename at all.

Every identifier here has structure that OCR does not know about: PAN is
positional (letters, then digits, then a letter), Aadhaar carries a Verhoeff
check digit, IFSC has a fixed zero in position five. That structure is what makes
repair possible -- a character in a digit-only position can only be a digit, so
`I` there is unambiguously `1`. Where the structure says the value is still wrong
after repair, we say so rather than guessing further.

The rule throughout: repair what the format proves, reject what it disproves, and
never invent. A rejected identifier costs a filename segment; a wrong one that
looks right costs an audit.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# --------------------------------------------------------------------------- #
# OCR confusion tables
# --------------------------------------------------------------------------- #

#: Glyph confusions, resolved by position. Applied only where the format proves
#: what the character must be, so these are corrections rather than guesses.
_TO_DIGIT = str.maketrans(
    {
        "O": "0",
        "Q": "0",
        "D": "0",
        "I": "1",
        "L": "1",
        "S": "5",
        "B": "8",
        "Z": "2",
        "G": "6",
        "T": "7",
    }
)
_TO_LETTER = str.maketrans({"0": "O", "1": "I", "5": "S", "8": "B", "2": "Z", "6": "G"})

_NON_ALNUM = re.compile(r"[^A-Za-z0-9]")

_PAN_RE = re.compile(r"^[A-Z]{5}[0-9]{4}[A-Z]$")
_AADHAAR_RE = re.compile(r"^[2-9][0-9]{11}$")
_IFSC_RE = re.compile(r"^[A-Z]{4}0[A-Z0-9]{6}$")


@dataclass(frozen=True, slots=True)
class ValidatedIdentifier:
    """Outcome of checking one identifier.

    ``value`` is the canonical form when valid, and the best-effort cleaned form
    when not -- callers decide whether to use it. Keeping the rejected value
    visible matters: HR needs to see *what* was misread to find the right page in
    the scan.
    """

    value: str
    is_valid: bool
    repaired: bool = False
    note: str = ""

    @property
    def usable(self) -> bool:
        """Safe to put in a filename."""
        return self.is_valid and bool(self.value)


def _clean(raw: str) -> str:
    return _NON_ALNUM.sub("", raw or "").upper()


# --------------------------------------------------------------------------- #
# PAN
# --------------------------------------------------------------------------- #


def validate_pan(raw: str) -> ValidatedIdentifier:
    """Validate a 10-character PAN, repairing positional OCR confusions.

    Layout is ``AAAAA9999A``: five letters, four digits, one letter. Because the
    class of every position is fixed, a digit appearing among the leading letters
    can only be a misread letter and vice versa -- so repair here is deterministic,
    not probabilistic.

    The fourth character encodes holder type (``P`` individual, ``C`` company,
    ``H`` HUF, ``F`` firm...) and the fifth is the surname initial. Neither is
    checked: unusual-but-legal combinations exist, and rejecting a real PAN is
    worse than accepting an odd one that still matches the layout.
    """
    cleaned = _clean(raw)
    if not cleaned:
        return ValidatedIdentifier("", False)

    if len(cleaned) != 10:
        return ValidatedIdentifier(
            cleaned,
            False,
            note=f"PAN must be 10 characters, read {len(cleaned)}.",
        )

    repaired = (
        cleaned[:5].translate(_TO_LETTER)
        + cleaned[5:9].translate(_TO_DIGIT)
        + cleaned[9:].translate(_TO_LETTER)
    )
    if not _PAN_RE.match(repaired):
        return ValidatedIdentifier(
            repaired, False, note=f"'{cleaned}' does not match the PAN format AAAAA9999A."
        )

    changed = repaired != cleaned
    return ValidatedIdentifier(
        repaired,
        True,
        repaired=changed,
        note=(f"Corrected likely OCR misreads: '{cleaned}' -> '{repaired}'." if changed else ""),
    )


# --------------------------------------------------------------------------- #
# Aadhaar -- Verhoeff checksum
# --------------------------------------------------------------------------- #

# Dihedral group D5 multiplication table.
_D = (
    (0, 1, 2, 3, 4, 5, 6, 7, 8, 9),
    (1, 2, 3, 4, 0, 6, 7, 8, 9, 5),
    (2, 3, 4, 0, 1, 7, 8, 9, 5, 6),
    (3, 4, 0, 1, 2, 8, 9, 5, 6, 7),
    (4, 0, 1, 2, 3, 9, 5, 6, 7, 8),
    (5, 9, 8, 7, 6, 0, 4, 3, 2, 1),
    (6, 5, 9, 8, 7, 1, 0, 4, 3, 2),
    (7, 6, 5, 9, 8, 2, 1, 0, 4, 3),
    (8, 7, 6, 5, 9, 3, 2, 1, 0, 4),
    (9, 8, 7, 6, 5, 4, 3, 2, 1, 0),
)

# Permutation applied per position, cycling with period 8.
_P = (
    (0, 1, 2, 3, 4, 5, 6, 7, 8, 9),
    (1, 5, 7, 6, 2, 8, 3, 0, 9, 4),
    (5, 8, 0, 3, 7, 9, 6, 1, 4, 2),
    (8, 9, 1, 6, 0, 4, 3, 5, 2, 7),
    (9, 4, 5, 3, 1, 2, 6, 8, 7, 0),
    (4, 2, 8, 6, 5, 7, 3, 9, 0, 1),
    (2, 7, 9, 3, 8, 0, 6, 4, 1, 5),
    (7, 0, 4, 6, 9, 1, 3, 2, 5, 8),
)

_INV = (0, 4, 3, 2, 1, 5, 6, 7, 8, 9)


def verhoeff_checksum(digits: str) -> int:
    """Verhoeff checksum of a digit string. Zero means the number is intact."""
    check = 0
    for index, char in enumerate(reversed(digits)):
        check = _D[check][_P[index % 8][int(char)]]
    return check


def verhoeff_check_digit(digits: str) -> int:
    """The digit that would make ``digits`` a valid Verhoeff number."""
    return _INV[verhoeff_checksum(digits + "0")]


def validate_aadhaar(raw: str) -> ValidatedIdentifier:
    """Validate a 12-digit Aadhaar number.

    UIDAI appends a Verhoeff check digit, which catches every single-digit error
    and every adjacent transposition -- precisely the two mistakes OCR makes. A
    length check alone would pass `123456789012`, which is not an Aadhaar number
    and would sail into a filename looking entirely plausible.

    The number also cannot begin with 0 or 1, which is checked because the model
    sometimes returns a VID or an enrolment number instead.

    Note the asymmetry with PAN: letters are repaired to digits first, but if the
    checksum then fails we stop. Nothing in the format says *which* digit is
    wrong, so any further "repair" would be fabrication.
    """
    cleaned = _clean(raw)
    if not cleaned:
        return ValidatedIdentifier("", False)

    digits = cleaned.translate(_TO_DIGIT)
    repaired = digits != cleaned

    if len(digits) != 12 or not digits.isdigit():
        return ValidatedIdentifier(
            digits, False, note=f"Aadhaar must be 12 digits, read {len(cleaned)} characters."
        )
    if not _AADHAAR_RE.match(digits):
        return ValidatedIdentifier(digits, False, note="Aadhaar numbers do not start with 0 or 1.")
    if verhoeff_checksum(digits) != 0:
        return ValidatedIdentifier(
            digits,
            False,
            repaired=repaired,
            note="Aadhaar checksum failed; the number was misread or is not genuine.",
        )

    return ValidatedIdentifier(
        digits,
        True,
        repaired=repaired,
        note=("Corrected likely OCR misreads in the Aadhaar number." if repaired else ""),
    )


# --------------------------------------------------------------------------- #
# IFSC
# --------------------------------------------------------------------------- #


def validate_ifsc(raw: str) -> ValidatedIdentifier:
    """Validate an 11-character IFSC code (``ABCD0123456``).

    The fifth character is always ``0``, reserved by RBI. That fixed position is
    the most common OCR casualty, since `O` and `0` are the confusion this whole
    module exists for.
    """
    cleaned = _clean(raw)
    if not cleaned:
        return ValidatedIdentifier("", False)
    if len(cleaned) != 11:
        return ValidatedIdentifier(
            cleaned, False, note=f"IFSC must be 11 characters, read {len(cleaned)}."
        )

    repaired = cleaned[:4].translate(_TO_LETTER) + "0" + cleaned[5:]
    if not _IFSC_RE.match(repaired):
        return ValidatedIdentifier(
            repaired, False, note=f"'{cleaned}' does not match the IFSC format ABCD0123456."
        )

    changed = repaired != cleaned
    return ValidatedIdentifier(
        repaired,
        True,
        repaired=changed,
        note=(f"Corrected likely OCR misreads: '{cleaned}' -> '{repaired}'." if changed else ""),
    )
