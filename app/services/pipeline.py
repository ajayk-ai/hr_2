"""Batch orchestration: OCR -> classify -> name -> order -> package.

Failure policy: a document that fails OCR or classification is recorded in the
report with its error and excluded from the ZIP; the rest of the batch still
completes. HR uploading eight files should not lose seven of them because one
scan was corrupt.
"""

from __future__ import annotations

import asyncio
import io
import time
import uuid
from collections import Counter, defaultdict
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime

from app.config import Settings
from app.domain import naming
from app.domain.document_types import (
    DEFAULT_REQUIRED_TYPES,
    DocumentType,
    coerce_document_type,
)
from app.domain.schemas import (
    DocumentClassification,
    ExtractedFields,
    FileReport,
    ProcessingReport,
)
from app.domain.validation import (
    ValidatedIdentifier,
    validate_aadhaar,
    validate_ifsc,
    validate_pan,
)
from app.errors import AppError
from app.logging_config import get_logger
from app.services.classifier import DocumentClassifier, truncate_ocr_text
from app.services.ocr import OcrEngine, OcrResult
from app.services.packaging import build_zip
from app.utils.uploads import LoadedUpload

logger = get_logger(__name__)

#: Identity documents carry the authoritative spelling of the employee's name.
_NAME_AUTHORITY: dict[DocumentType, float] = defaultdict(
    lambda: 1.0,
    {
        DocumentType.AADHAAR: 3.0,
        DocumentType.PAN: 3.0,
        DocumentType.PASSPORT: 3.0,
        DocumentType.OFFER_LETTER: 1.5,
    },
)


@dataclass(slots=True)
class _Outcome:
    """Per-file intermediate state, before names and order are assigned."""

    upload: LoadedUpload
    document_type: DocumentType = DocumentType.UNKNOWN
    confidence: float = 0.0
    fields: ExtractedFields = field(default_factory=ExtractedFields)
    reasoning: str = ""
    ocr: OcrResult | None = None
    warnings: list[str] = field(default_factory=list)
    error: str | None = None
    output_filename: str | None = None
    """Assigned once naming has run. Stays None for files excluded from the ZIP."""
    duplicate_of: str | None = None
    identifier_notes: list[str] = field(default_factory=list)
    name_mismatch: str | None = None

    @property
    def succeeded(self) -> bool:
        return self.error is None

    @property
    def deliverable(self) -> bool:
        """Belongs in the ZIP: processed cleanly and not a copy of another upload."""
        return self.error is None and self.duplicate_of is None


@dataclass(frozen=True, slots=True)
class PipelineResult:
    request_id: str
    report: ProcessingReport
    archive: io.BytesIO


class DocumentPipeline:
    def __init__(
        self,
        *,
        settings: Settings,
        ocr_engine: OcrEngine,
        classifier: DocumentClassifier,
    ) -> None:
        self._settings = settings
        self._ocr = ocr_engine
        self._classifier = classifier

    async def run(
        self,
        uploads: list[LoadedUpload],
        *,
        candidate_name: str | None = None,
        required_types: tuple[DocumentType, ...] = DEFAULT_REQUIRED_TYPES,
        request_id: str | None = None,
        pr_number: str | None = None,
    ) -> PipelineResult:
        # Callers pass the HTTP request id so logs, the response header and
        # report.json can be correlated; a fresh one is minted for direct use.
        request_id = request_id or uuid.uuid4().hex
        started = time.perf_counter()
        log = logger.bind(request_id=request_id, file_count=len(uploads))
        log.info("pipeline.started")

        # Deduplicate before the fan-out, not after: an identical re-upload
        # otherwise costs a full OCR page charge and an LLM call to produce a file
        # that gets discarded anyway.
        unique, duplicates = _partition_duplicates(uploads)
        if duplicates:
            log.info("pipeline.duplicates_skipped", count=len(duplicates))

        outcomes: list[_Outcome] = list(
            await asyncio.gather(
                *(self._process_one(upload, request_id=request_id) for upload in unique)
            )
        )
        outcomes.extend(duplicates)

        resolved_name = candidate_name.strip() if candidate_name else None
        resolved_name = resolved_name or _infer_candidate_name(outcomes)
        resolved_pr_number = pr_number.strip() if pr_number and pr_number.strip() else None

        successful = sorted(
            (o for o in outcomes if o.deliverable),
            key=lambda o: naming.sort_key(o.document_type, o.fields, o.upload.upload_index),
        )

        filenames = naming.deduplicate(
            [
                naming.build_filename(
                    candidate_name=resolved_name or "",
                    document_type=outcome.document_type,
                    fields=outcome.fields,
                    original_filename=outcome.upload.original_filename,
                    content_type=outcome.upload.content_type,
                    sequence=index,
                    request_id=request_id,
                    pr_number=resolved_pr_number,
                    mask_sensitive=self._settings.mask_sensitive_ids,
                )
                for index, outcome in enumerate(successful, start=1)
            ]
        )
        for outcome, filename in zip(successful, filenames, strict=True):
            outcome.output_filename = filename

        _flag_name_mismatches(outcomes, resolved_name)

        report = self._build_report(
            request_id=request_id,
            candidate_name=resolved_name,
            pr_number=resolved_pr_number,
            outcomes=outcomes,
            successful=successful,
            required_types=required_types,
            elapsed_ms=int((time.perf_counter() - started) * 1000),
        )

        archive = build_zip(
            zip(filenames, (o.upload.content for o in successful), strict=True), report
        )

        log.info(
            "pipeline.completed",
            duration_ms=report.processing_ms,
            failed=report.failed_count,
            needs_review=len(report.needs_review),
        )
        return PipelineResult(request_id=request_id, report=report, archive=archive)

    async def _process_one(self, upload: LoadedUpload, *, request_id: str) -> _Outcome:
        log = logger.bind(request_id=request_id, filename=upload.original_filename)
        outcome = _Outcome(upload=upload)

        try:
            ocr = await self._ocr.extract(
                content=upload.content,
                mime_type=upload.content_type,
                filename=upload.original_filename,
            )
        except AppError as exc:
            log.warning("pipeline.ocr_failed", code=exc.code, detail=exc.detail)
            outcome.error = exc.message
            return outcome
        except Exception as exc:
            log.exception("pipeline.ocr_unexpected_error")
            outcome.error = f"Unexpected OCR error: {type(exc).__name__}"
            return outcome

        outcome.ocr = ocr
        outcome.warnings.extend(ocr.warnings)

        truncated = truncate_ocr_text(ocr.text, self._settings.ocr_text_char_budget)
        if len(truncated) < len(ocr.text):
            outcome.warnings.append(
                "Document text was truncated before classification; fields near the "
                "middle of the document may have been missed."
            )

        try:
            classification = await self._classifier.classify(
                ocr=OcrResult(
                    text=truncated,
                    page_count=ocr.page_count,
                    confidence=ocr.confidence,
                    entities=ocr.entities,
                ),
                filename=upload.original_filename,
                content_type=upload.content_type,
            )
        except AppError as exc:
            log.warning("pipeline.classification_failed", code=exc.code, detail=exc.detail)
            outcome.error = exc.message
            return outcome
        except Exception as exc:
            log.exception("pipeline.classification_unexpected_error")
            outcome.error = f"Unexpected classification error: {type(exc).__name__}"
            return outcome

        self._apply_classification(outcome, classification)
        log.info(
            "pipeline.classified",
            document_type=outcome.document_type.value,
            confidence=round(outcome.confidence, 3),
        )
        return outcome

    def _apply_classification(
        self, outcome: _Outcome, classification: DocumentClassification
    ) -> None:
        document_type = coerce_document_type(classification.document_type)
        confidence = classification.confidence

        if (
            document_type is not DocumentType.UNKNOWN
            and confidence < self._settings.min_classification_confidence
        ):
            outcome.warnings.append(
                f"Classified as {document_type.value} with low confidence "
                f"({confidence:.2f}); filed as Unknown for manual review."
            )
            document_type = DocumentType.UNKNOWN

        outcome.document_type = document_type
        outcome.confidence = confidence
        outcome.fields = _validate_identifiers(classification.fields, outcome)
        outcome.reasoning = classification.reasoning

    def _build_report(
        self,
        *,
        request_id: str,
        candidate_name: str | None,
        pr_number: str | None,
        outcomes: list[_Outcome],
        successful: list[_Outcome],
        required_types: tuple[DocumentType, ...],
        elapsed_ms: int,
    ) -> ProcessingReport:
        file_reports = [
            FileReport(
                original_filename=outcome.upload.original_filename,
                output_filename=outcome.output_filename,
                document_type=outcome.document_type,
                confidence=round(outcome.confidence, 3),
                content_type=outcome.upload.content_type,
                size_bytes=outcome.upload.size_bytes,
                ocr_confidence=(
                    round(outcome.ocr.confidence, 3)
                    if outcome.ocr and outcome.ocr.confidence is not None
                    else None
                ),
                page_count=outcome.ocr.page_count if outcome.ocr else None,
                extracted_fields=_report_fields(
                    outcome.fields, mask=self._settings.mask_sensitive_ids
                ),
                reasoning=outcome.reasoning,
                warnings=outcome.warnings,
                error=outcome.error,
                duplicate_of=outcome.duplicate_of,
            )
            # Reported in upload order so HR can reconcile against what they sent,
            # independent of the filing order used inside the ZIP.
            for outcome in sorted(outcomes, key=lambda o: o.upload.upload_index)
        ]

        present = Counter(o.document_type for o in successful)
        missing = [t for t in required_types if present[t] == 0]
        duplicates = [
            document_type
            for document_type, count in present.items()
            if count > 1 and document_type not in _MULTI_ALLOWED
        ]
        needs_review = [
            report.output_filename
            for report in file_reports
            if report.output_filename
            and (report.confidence < self._settings.review_confidence or report.warnings)
        ]

        return ProcessingReport(
            request_id=request_id,
            generated_at=datetime.now(UTC),
            candidate_name=candidate_name,
            pr_number=pr_number,
            files=file_reports,
            missing_document_types=missing,
            duplicate_document_types=duplicates,
            duplicate_uploads=sum(1 for o in outcomes if o.duplicate_of is not None),
            identifier_warnings=[
                f"{o.upload.original_filename}: {note}"
                for o in sorted(outcomes, key=lambda o: o.upload.upload_index)
                for note in o.identifier_notes
            ],
            name_mismatches=[
                f"{o.upload.original_filename}: {o.name_mismatch}"
                for o in sorted(outcomes, key=lambda o: o.upload.upload_index)
                if o.name_mismatch
            ],
            needs_review=needs_review,
            processing_ms=elapsed_ms,
        )


#: Types where several documents per candidate is normal, so a repeat is not a
#: duplicate worth flagging.
_MULTI_ALLOWED: frozenset[DocumentType] = frozenset(
    {
        DocumentType.SALARY_SLIP,
        DocumentType.MARKSHEET,
        DocumentType.BANK_STATEMENT,
        DocumentType.EXPERIENCE_LETTER,
        DocumentType.RELIEVING_LETTER,
        DocumentType.OFFER_LETTER,
        DocumentType.OTHER,
        DocumentType.UNKNOWN,
    }
)

_MASKED_REPORT_FIELDS = ("aadhaar_number", "bank_account_number", "passport_number")


def _report_fields(fields: ExtractedFields, *, mask: bool) -> dict[str, object]:
    from app.utils.redaction import mask_identifier

    data = fields.as_dict()
    if mask:
        for key in _MASKED_REPORT_FIELDS:
            if key in data:
                data[key] = mask_identifier(str(data[key]), visible=4)
    return data


#: Identifier field -> validator. Each returns a repaired value or an explanation.
_IDENTIFIER_VALIDATORS: dict[str, Callable[[str], ValidatedIdentifier]] = {
    "pan_number": validate_pan,
    "aadhaar_number": validate_aadhaar,
    "ifsc_code": validate_ifsc,
}


#: A field the model found nothing for should come back as "", but constrained
#: decoding on an empty answer occasionally degenerates into a repeated phrase
#: instead -- observed in production as a several-thousand-character IFSC
#: "reading" of "not present in document text. Leaving empty." repeated hundreds
#: of times. Validation already rejects it correctly; this is what stops that
#: text from being dumped whole into a warning box, the report, and the UI.
_RAW_PREVIEW_LIMIT = 48


def _preview(raw: str) -> str:
    raw = raw.strip()
    if len(raw) <= _RAW_PREVIEW_LIMIT:
        return raw
    return raw[:_RAW_PREVIEW_LIMIT].rstrip() + "…"


def _validate_identifiers(fields: ExtractedFields, outcome: _Outcome) -> ExtractedFields:
    """Repair or reject each government identifier before it reaches a filename.

    A value that fails validation is *cleared*, not kept. Naming falls back to the
    next-best identifier, so the file is still delivered and still findable -- it
    simply does not carry a number that would not survive being typed into a
    verification portal. The rejected reading stays in the warning so the page can
    be found in the original scan.
    """
    updates: dict[str, str] = {}

    for field_name, validator in _IDENTIFIER_VALIDATORS.items():
        raw = getattr(fields, field_name, "")
        if not raw:
            continue

        result = validator(raw)
        label = field_name.replace("_", " ")

        if result.usable:
            if result.repaired:
                updates[field_name] = result.value
                outcome.warnings.append(f"Repaired {label}: {result.note}")
                outcome.identifier_notes.append(result.note)
            continue

        updates[field_name] = ""
        message = result.note or f"The {label} could not be validated."
        outcome.warnings.append(f"Discarded {label} '{_preview(raw)}'. {message}")
        outcome.identifier_notes.append(f"{label}: {message}")

    return fields.model_copy(update=updates) if updates else fields


def _flag_name_mismatches(outcomes: list[_Outcome], resolved_name: str | None) -> None:
    """Warn where a document's printed name disagrees with the batch.

    The batch name is decided by weighted vote, so a single odd document loses
    rather than dragging every filename with it. That is the right outcome for
    naming and the wrong one for review: the losing document is exactly the one
    worth a second look, because the usual cause is a page belonging to somebody
    else entirely. Voting hides it; this surfaces it.
    """
    if not resolved_name:
        return
    for outcome in outcomes:
        printed = outcome.fields.full_name
        if not outcome.deliverable or not printed:
            continue
        if naming.names_are_compatible(printed, resolved_name):
            continue
        outcome.name_mismatch = (
            f"Printed name '{printed}' does not match '{resolved_name}' used for "
            "the rest of the batch; check this is the same person's document."
        )
        outcome.warnings.append(outcome.name_mismatch)


def _partition_duplicates(
    uploads: list[LoadedUpload],
) -> tuple[list[LoadedUpload], list[_Outcome]]:
    """Split uploads into ones to process and ones that are exact copies.

    Byte equality only. Two scans of the same PAN card differ in every pixel and
    are *not* caught here -- that is a semantic judgement, and the report's
    duplicate-document-type check is where it belongs. What this catches is the
    common clerical case: the same file attached twice, or a file re-sent under a
    new name after an upload appeared to fail.

    The earliest upload wins so the kept file is the one HR listed first.
    """
    seen: dict[str, LoadedUpload] = {}
    unique: list[LoadedUpload] = []
    duplicates: list[_Outcome] = []

    for upload in sorted(uploads, key=lambda u: u.upload_index):
        original = seen.get(upload.content_sha256)
        if original is None:
            seen[upload.content_sha256] = upload
            unique.append(upload)
            continue
        duplicates.append(
            _Outcome(
                upload=upload,
                duplicate_of=original.original_filename,
                warnings=[
                    f"Byte-identical to '{original.original_filename}'; skipped so the "
                    "ZIP contains one copy."
                ],
            )
        )
    return unique, duplicates


def _infer_candidate_name(outcomes: list[_Outcome]) -> str | None:
    """Pick one spelling of the employee's name for the whole batch.

    Documents disagree -- an Aadhaar says "RAVI KUMAR SHARMA", a resume says "Ravi
    Sharma". Using each document's own reading would scatter the batch across
    several name prefixes and defeat the point of renaming. Votes are weighted by
    classification confidence and by how authoritative the document type is for
    legal names.
    """
    scores: defaultdict[str, float] = defaultdict(float)
    for outcome in outcomes:
        if not outcome.succeeded:
            continue
        name = naming.normalise_name_segment(outcome.fields.full_name)
        if not name:
            continue
        scores[outcome.fields.full_name.strip()] += (
            max(outcome.confidence, 0.1) * _NAME_AUTHORITY[outcome.document_type]
        )
    if not scores:
        return None
    return max(scores.items(), key=lambda item: item[1])[0]
