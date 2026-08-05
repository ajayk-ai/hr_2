"""Pydantic contracts: the LLM output schema and the public API models.

Note on the LLM schema (:class:`ExtractedFields` / :class:`DocumentClassification`):
absent values are represented as empty strings and zeros rather than ``null``.
Constrained decoding against a schema full of ``anyOf: [T, null]`` branches is
measurably more fragile than one with flat, always-present fields, and the model
is far likelier to return a well-formed object first try. ``as_dict`` strips the
sentinels so nothing downstream has to know about this.
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any

from pydantic import BaseModel, ConfigDict, Field

from app.domain.document_types import DocumentType

# --------------------------------------------------------------------------- #
# LLM output schema
# --------------------------------------------------------------------------- #


class ExtractedFields(BaseModel):
    """Fields lifted from a document. Every field is optional in practice --
    use the empty string / 0 to mean "not present in this document"."""

    model_config = ConfigDict(extra="ignore")

    full_name: str = Field(
        default="",
        description="Name of the candidate as printed on the document. Empty if absent.",
    )
    document_date: str = Field(
        default="",
        description="Primary date on the document as YYYY-MM-DD, or YYYY-MM if only a month is given.",
    )
    aadhaar_number: str = Field(default="", description="12-digit Aadhaar number, digits only.")
    pan_number: str = Field(default="", description="10-character PAN, e.g. ABCDE1234F.")
    passport_number: str = Field(default="", description="Passport number.")
    employer_name: str = Field(default="", description="Employer/company name.")
    pay_period_month: Annotated[int, Field(ge=0, le=12)] = Field(
        default=0, description="Payslip month as 1-12. 0 if not a payslip."
    )
    pay_period_year: Annotated[int, Field(ge=0, le=2999)] = Field(
        default=0, description="Payslip/statement year as YYYY. 0 if unknown."
    )
    institution_name: str = Field(default="", description="School, college or university name.")
    qualification: str = Field(
        default="", description="Qualification or exam, e.g. 'BTech', 'Class12', 'MBA'."
    )
    exam_year: Annotated[int, Field(ge=0, le=2999)] = Field(
        default=0, description="Year of passing as YYYY. 0 if unknown."
    )
    bank_account_number: str = Field(default="", description="Bank account number.")
    ifsc_code: str = Field(default="", description="Bank IFSC code.")

    def as_dict(self) -> dict[str, Any]:
        """Populated fields only, with empty-string/zero sentinels dropped."""
        return {k: v for k, v in self.model_dump().items() if v not in ("", 0)}


class DocumentClassification(BaseModel):
    """Exactly what the LLM is asked to return for one document.

    Deliberately *not* included: the output filename and the sort position. Both
    are derived by :mod:`app.domain.naming` from these fields, so naming stays
    deterministic, unit-testable, and immune to prompt drift.
    """

    model_config = ConfigDict(extra="ignore")

    document_type: str = Field(
        description="One of the allowed document type values, matched exactly."
    )
    confidence: Annotated[float, Field(ge=0.0, le=1.0)] = Field(
        description="Confidence in document_type, 0.0-1.0. Be honest; low values are expected."
    )
    fields: ExtractedFields = Field(
        default_factory=ExtractedFields, description="Fields extracted from the document."
    )
    reasoning: str = Field(
        default="",
        description="One short sentence naming the evidence that decided the type.",
    )


# --------------------------------------------------------------------------- #
# API models
# --------------------------------------------------------------------------- #


class FileReport(BaseModel):
    """Per-file outcome, included in the ZIP as part of ``report.json``."""

    original_filename: str
    output_filename: str | None = Field(
        default=None, description="Null when the file could not be included in the ZIP."
    )
    document_type: DocumentType
    confidence: float
    content_type: str
    size_bytes: int
    ocr_confidence: float | None = None
    page_count: int | None = None
    extracted_fields: dict[str, Any] = Field(default_factory=dict)
    reasoning: str = ""
    warnings: list[str] = Field(default_factory=list)
    error: str | None = None

    @property
    def succeeded(self) -> bool:
        return self.error is None


class ProcessingReport(BaseModel):
    """The batch-level summary written to ``report.json`` inside the ZIP."""

    request_id: str
    generated_at: datetime
    candidate_name: str | None = None
    files: list[FileReport]
    missing_document_types: list[DocumentType] = Field(default_factory=list)
    duplicate_document_types: list[DocumentType] = Field(default_factory=list)
    needs_review: list[str] = Field(
        default_factory=list,
        description="Output filenames a human should check before the file is accepted.",
    )
    processing_ms: int

    @property
    def failed_count(self) -> int:
        return sum(1 for f in self.files if not f.succeeded)


class HealthResponse(BaseModel):
    status: str
    version: str
    environment: str
    ocr_engine: str
    classifier: str
