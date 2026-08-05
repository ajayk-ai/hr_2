"""The document taxonomy and everything that is derived from it.

This module is the single source of truth for which document types exist, what
order they are filed in, and which fields each type is expected to yield. The
LLM prompt, the naming rules and the completeness check all read from here, so
adding a new document type is a one-place change.
"""

from __future__ import annotations

from enum import StrEnum


class DocumentType(StrEnum):
    PHOTOGRAPH = "Photograph"
    AADHAAR = "Aadhaar"
    PAN = "PAN"
    PASSPORT = "Passport"
    RESUME = "Resume"
    OFFER_LETTER = "OfferLetter"
    RELIEVING_LETTER = "RelievingLetter"
    EXPERIENCE_LETTER = "ExperienceLetter"
    SALARY_SLIP = "SalarySlip"
    BANK_STATEMENT = "BankStatement"
    CANCELLED_CHEQUE = "CancelledCheque"
    MARKSHEET = "Marksheet"
    DEGREE_CERTIFICATE = "DegreeCertificate"
    OTHER = "Other"
    UNKNOWN = "Unknown"


#: Filing order for the delivered ZIP. Identity first, then employment history,
#: then education -- the sequence an HR reviewer works through a joining file in.
CANONICAL_ORDER: tuple[DocumentType, ...] = (
    DocumentType.PHOTOGRAPH,
    DocumentType.AADHAAR,
    DocumentType.PAN,
    DocumentType.PASSPORT,
    DocumentType.RESUME,
    DocumentType.OFFER_LETTER,
    DocumentType.RELIEVING_LETTER,
    DocumentType.EXPERIENCE_LETTER,
    DocumentType.SALARY_SLIP,
    DocumentType.BANK_STATEMENT,
    DocumentType.CANCELLED_CHEQUE,
    DocumentType.MARKSHEET,
    DocumentType.DEGREE_CERTIFICATE,
    DocumentType.OTHER,
    DocumentType.UNKNOWN,
)

_ORDER_INDEX: dict[DocumentType, int] = {t: i for i, t in enumerate(CANONICAL_ORDER)}

#: Types whose identifier is a government ID we mask before it reaches a filename
#: or the JSON report.
SENSITIVE_ID_TYPES: frozenset[DocumentType] = frozenset(
    {DocumentType.AADHAAR, DocumentType.BANK_STATEMENT, DocumentType.CANCELLED_CHEQUE}
)

#: Short hints shown to the LLM so it knows what evidence separates the types.
TYPE_HINTS: dict[DocumentType, str] = {
    DocumentType.PHOTOGRAPH: "A portrait/passport photo with little or no text.",
    DocumentType.AADHAAR: (
        "UIDAI Aadhaar card. Look for 'Government of India', 'Unique Identification "
        "Authority', a 12-digit number, VID, or 'आधार'."
    ),
    DocumentType.PAN: (
        "Income Tax PAN card. Look for 'Permanent Account Number' and a "
        "5-letter/4-digit/1-letter code."
    ),
    DocumentType.PASSPORT: "Indian passport data page: passport number, MRZ lines, place of issue.",
    DocumentType.RESUME: "CV / resume: work history, skills, education sections.",
    DocumentType.OFFER_LETTER: "Employment offer or appointment letter stating a joining date and CTC.",
    DocumentType.RELIEVING_LETTER: "Confirms an employee was relieved on a last working day.",
    DocumentType.EXPERIENCE_LETTER: "Certifies a period of employment and designation.",
    DocumentType.SALARY_SLIP: "Monthly payslip: earnings/deductions table, net pay, pay period.",
    DocumentType.BANK_STATEMENT: "Bank account statement with a transaction ledger.",
    DocumentType.CANCELLED_CHEQUE: "A cheque leaf marked cancelled, showing IFSC and account number.",
    DocumentType.MARKSHEET: "Examination marksheet with subject-wise marks for a semester or year.",
    DocumentType.DEGREE_CERTIFICATE: "Degree/diploma conferral certificate from a university.",
    DocumentType.OTHER: "A legible document that fits none of the above.",
    DocumentType.UNKNOWN: "Illegible, blank, or impossible to identify.",
}

#: Documents a complete joining file is expected to contain. Anything missing is
#: reported back to HR rather than silently ignored.
DEFAULT_REQUIRED_TYPES: tuple[DocumentType, ...] = (
    DocumentType.PHOTOGRAPH,
    DocumentType.AADHAAR,
    DocumentType.PAN,
    DocumentType.RESUME,
    DocumentType.SALARY_SLIP,
)


def order_index(document_type: DocumentType) -> int:
    """Position of ``document_type`` in the canonical filing order."""
    return _ORDER_INDEX.get(document_type, len(CANONICAL_ORDER))


def coerce_document_type(raw: str | None) -> DocumentType:
    """Map an arbitrary model-produced string onto the taxonomy.

    Falls back to ``UNKNOWN`` rather than raising: a mislabelled document should
    surface in the report for review, not fail the whole batch.
    """
    if not raw:
        return DocumentType.UNKNOWN
    normalised = raw.strip().replace("_", "").replace(" ", "").replace("-", "").lower()
    for member in DocumentType:
        if member.value.lower() == normalised:
            return member
    return DocumentType.UNKNOWN
