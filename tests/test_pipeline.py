from __future__ import annotations

import hashlib
import json
import zipfile

import pytest

from app.config import Settings
from app.domain.document_types import DocumentType
from app.domain.schemas import DocumentClassification, ExtractedFields, ProcessingReport
from app.services.ocr import OcrResult
from app.services.pipeline import DocumentPipeline
from app.utils.uploads import LoadedUpload
from tests.conftest import PDF_BYTES, PNG_BYTES, VALID_AADHAAR, FakeClassifier, FakeOcrEngine

pytestmark = pytest.mark.asyncio

# Every filename `pipeline.run` produces is now tagged with the batch's request
# id, so tests asserting an exact filename need a deterministic one to assert
# against -- `pipeline.run` would otherwise mint a fresh `uuid.uuid4().hex` per
# call, and the expected string would never match twice.
REQUEST_ID = "deadbeef-0000-0000-0000-000000000000"
ID_TAG = REQUEST_ID[:8]


def upload(
    name: str, *, index: int, content: bytes | None = None, ctype: str = "application/pdf"
) -> LoadedUpload:
    """One upload, with bytes distinct per filename unless told otherwise.

    Distinctness is the default because the pipeline now skips byte-identical
    uploads: a shared constant would collapse every multi-file fixture into a
    single document plus duplicates. Tests that *want* a duplicate pass the same
    `content` explicitly, which is exactly what the real case looks like.
    """
    if content is None:
        content = PDF_BYTES + f"%{name}\n".encode()
    return LoadedUpload(
        original_filename=name,
        content_type=ctype,
        content=content,
        upload_index=index,
        # Derived exactly as `load_upload` does.
        content_sha256=hashlib.sha256(content).hexdigest(),
    )


def ocr(text: str = "text", pages: int = 1, confidence: float | None = 0.95) -> OcrResult:
    return OcrResult(text=text, page_count=pages, confidence=confidence)


def classification(
    doc_type: DocumentType, confidence: float = 0.95, **fields: object
) -> DocumentClassification:
    return DocumentClassification(
        document_type=doc_type.value,
        confidence=confidence,
        fields=ExtractedFields(**fields),  # type: ignore[arg-type]
        reasoning="test",
    )


def make_pipeline(
    settings: Settings, ocr_results: dict, classifications: dict
) -> tuple[DocumentPipeline, FakeOcrEngine, FakeClassifier]:
    engine = FakeOcrEngine(ocr_results)
    classifier = FakeClassifier(classifications)
    return (
        DocumentPipeline(settings=settings, ocr_engine=engine, classifier=classifier),
        engine,
        classifier,
    )


def read_report(archive) -> ProcessingReport:
    with zipfile.ZipFile(archive) as zf:
        return ProcessingReport.model_validate(json.loads(zf.read("report.json")))


class TestHappyPath:
    async def test_files_are_renamed_and_filed_in_canonical_order(self, settings: Settings) -> None:
        uploads = [
            upload("scan_003.pdf", index=0),
            upload("scan_001.pdf", index=1),
            upload("selfie.png", index=2, content=PNG_BYTES, ctype="image/png"),
        ]
        pipeline, _, _ = make_pipeline(
            settings,
            {u.original_filename: ocr() for u in uploads},
            {
                "scan_003.pdf": classification(
                    DocumentType.MARKSHEET, qualification="BTech", exam_year=2018
                ),
                "scan_001.pdf": classification(
                    DocumentType.PAN, pan_number="ABCDE1234F", full_name="Ravi Kumar"
                ),
                "selfie.png": classification(DocumentType.PHOTOGRAPH),
            },
        )

        result = await pipeline.run(uploads, request_id=REQUEST_ID)

        with zipfile.ZipFile(result.archive) as zf:
            names = [n for n in zf.namelist() if n != "report.json"]

        assert names == [
            f"01_RaviKumar_Photograph_{ID_TAG}.png",
            f"02_RaviKumar_PAN_ABCDE1234F_{ID_TAG}.pdf",
            f"03_RaviKumar_Marksheet_BTech-2018_{ID_TAG}.pdf",
        ]

    async def test_every_file_is_tagged_with_the_batch_request_id(self, settings: Settings) -> None:
        """Guards the collision this feature exists to prevent: two different
        uploads extracted into the same folder must not be able to overwrite
        each other just because they got the same descriptive name."""
        uploads = [upload("a.pdf", index=0)]
        pipeline, _, _ = make_pipeline(
            settings, {"a.pdf": ocr()}, {"a.pdf": classification(DocumentType.RESUME)}
        )

        result = await pipeline.run(uploads, candidate_name="Ravi", request_id=REQUEST_ID)

        assert result.report.files[0].output_filename == f"01_Ravi_Resume_{ID_TAG}.pdf"

    async def test_original_bytes_are_preserved_exactly(self, settings: Settings) -> None:
        uploads = [upload("cv.pdf", index=0)]
        pipeline, _, _ = make_pipeline(
            settings, {"cv.pdf": ocr()}, {"cv.pdf": classification(DocumentType.RESUME)}
        )

        result = await pipeline.run(uploads, candidate_name="Ravi Kumar", request_id=REQUEST_ID)

        with zipfile.ZipFile(result.archive) as zf:
            assert zf.read(f"01_RaviKumar_Resume_{ID_TAG}.pdf") == uploads[0].content

    async def test_report_lists_files_in_upload_order(self, settings: Settings) -> None:
        uploads = [upload("b.pdf", index=0), upload("a.pdf", index=1)]
        pipeline, _, _ = make_pipeline(
            settings,
            {"a.pdf": ocr(), "b.pdf": ocr()},
            {
                "b.pdf": classification(DocumentType.MARKSHEET),
                "a.pdf": classification(DocumentType.PAN, pan_number="ABCDE1234F"),
            },
        )

        result = await pipeline.run(uploads, candidate_name="Ravi")

        assert [f.original_filename for f in result.report.files] == ["b.pdf", "a.pdf"]


class TestCandidateNameResolution:
    async def test_an_explicit_name_wins(self, settings: Settings) -> None:
        uploads = [upload("a.pdf", index=0)]
        pipeline, _, _ = make_pipeline(
            settings,
            {"a.pdf": ocr()},
            {
                "a.pdf": classification(
                    DocumentType.PAN, full_name="Wrong Name", pan_number="ABCDE1234F"
                )
            },
        )

        result = await pipeline.run(uploads, candidate_name="Ravi Kumar")

        assert result.report.candidate_name == "Ravi Kumar"
        assert result.report.files[0].output_filename.startswith("01_RaviKumar_")

    async def test_identity_documents_outweigh_a_resume(self, settings: Settings) -> None:
        # The resume says "Ravi Sharma"; the Aadhaar is authoritative for the legal name.
        uploads = [upload("cv.pdf", index=0), upload("id.pdf", index=1)]
        pipeline, _, _ = make_pipeline(
            settings,
            {"cv.pdf": ocr(), "id.pdf": ocr()},
            {
                "cv.pdf": classification(DocumentType.RESUME, full_name="Ravi Sharma"),
                "id.pdf": classification(
                    DocumentType.AADHAAR,
                    full_name="Ravi Kumar Sharma",
                    aadhaar_number=VALID_AADHAAR,
                ),
            },
        )

        result = await pipeline.run(uploads)

        assert result.report.candidate_name == "Ravi Kumar Sharma"

    async def test_one_name_is_used_for_every_file(self, settings: Settings) -> None:
        uploads = [upload("a.pdf", index=0), upload("b.pdf", index=1)]
        pipeline, _, _ = make_pipeline(
            settings,
            {"a.pdf": ocr(), "b.pdf": ocr()},
            {
                "a.pdf": classification(
                    DocumentType.PAN, full_name="RAVI KUMAR", pan_number="ABCDE1234F"
                ),
                "b.pdf": classification(DocumentType.RESUME, full_name="Ravi K."),
            },
        )

        result = await pipeline.run(uploads)
        prefixes = {f.output_filename.split("_")[1] for f in result.report.files}

        assert len(prefixes) == 1


class TestPartialFailure:
    async def test_a_failed_file_does_not_sink_the_batch(self, settings: Settings) -> None:
        uploads = [upload("good.pdf", index=0), upload("corrupt.pdf", index=1)]
        # FakeOcrEngine raises for any filename it has no canned result for.
        pipeline, _, _ = make_pipeline(
            settings,
            {"good.pdf": ocr()},
            {"good.pdf": classification(DocumentType.RESUME, full_name="Ravi")},
        )

        result = await pipeline.run(uploads, request_id=REQUEST_ID)

        with zipfile.ZipFile(result.archive) as zf:
            assert [n for n in zf.namelist() if n != "report.json"] == [
                f"01_Ravi_Resume_{ID_TAG}.pdf"
            ]

        failed = next(f for f in result.report.files if f.original_filename == "corrupt.pdf")
        assert failed.error is not None
        assert failed.output_filename is None
        assert result.report.failed_count == 1

    async def test_ocr_warnings_reach_the_report(self, settings: Settings) -> None:
        uploads = [upload("blurry.pdf", index=0)]
        pipeline, _, _ = make_pipeline(
            settings,
            {
                "blurry.pdf": OcrResult(
                    text="x", page_count=1, confidence=0.4, warnings=("Low scan quality.",)
                )
            },
            {"blurry.pdf": classification(DocumentType.RESUME, full_name="Ravi")},
        )

        result = await pipeline.run(uploads, request_id=REQUEST_ID)

        assert "Low scan quality." in result.report.files[0].warnings
        assert result.report.needs_review == [f"01_Ravi_Resume_{ID_TAG}.pdf"]


class TestConfidenceGate:
    async def test_low_confidence_is_filed_as_unknown(self, settings: Settings) -> None:
        uploads = [upload("maybe.pdf", index=0)]
        pipeline, _, _ = make_pipeline(
            settings,
            {"maybe.pdf": ocr()},
            {"maybe.pdf": classification(DocumentType.AADHAAR, confidence=0.3, full_name="Ravi")},
        )

        result = await pipeline.run(uploads, request_id=REQUEST_ID)
        report = result.report.files[0]

        assert report.document_type is DocumentType.UNKNOWN
        assert report.output_filename == f"01_Ravi_Unknown_{ID_TAG}.pdf"
        assert any("low confidence" in w for w in report.warnings)

    async def test_middling_confidence_is_filed_but_flagged(self, settings: Settings) -> None:
        # Between min_classification_confidence (0.55) and review_confidence (0.75):
        # good enough to file as itself, not good enough to trust unsupervised.
        uploads = [upload("id.pdf", index=0)]
        pipeline, _, _ = make_pipeline(
            settings,
            {"id.pdf": ocr()},
            {
                "id.pdf": classification(
                    DocumentType.PAN, confidence=0.65, pan_number="ABCDE1234F", full_name="Ravi"
                )
            },
        )

        result = await pipeline.run(uploads, request_id=REQUEST_ID)

        assert result.report.files[0].document_type is DocumentType.PAN
        assert result.report.needs_review == [f"01_Ravi_PAN_ABCDE1234F_{ID_TAG}.pdf"]

    async def test_confident_classifications_are_not_flagged(self, settings: Settings) -> None:
        uploads = [upload("id.pdf", index=0)]
        pipeline, _, _ = make_pipeline(
            settings,
            {"id.pdf": ocr()},
            {
                "id.pdf": classification(
                    DocumentType.PAN, confidence=0.98, pan_number="ABCDE1234F", full_name="Ravi"
                )
            },
        )

        result = await pipeline.run(uploads)

        assert result.report.needs_review == []

    async def test_confident_classifications_pass_through(self, settings: Settings) -> None:
        uploads = [upload("id.pdf", index=0)]
        pipeline, _, _ = make_pipeline(
            settings,
            {"id.pdf": ocr()},
            {
                "id.pdf": classification(
                    DocumentType.AADHAAR,
                    confidence=0.9,
                    aadhaar_number=VALID_AADHAAR,
                    full_name="Ravi",
                )
            },
        )

        result = await pipeline.run(uploads)

        assert result.report.files[0].document_type is DocumentType.AADHAAR


class TestDuplicateUploads:
    """The same bytes arriving twice: a re-send after an upload appeared to fail,
    or the same attachment added under two names."""

    async def test_an_identical_file_is_skipped_not_processed(self, settings: Settings) -> None:
        """Skipping before the fan-out is the point -- an OCR page charge and an
        LLM call for a file that would be discarded anyway is pure waste."""
        shared = PDF_BYTES + b"%identical\n"
        uploads = [
            upload("PAN.pdf", index=0, content=shared),
            upload("PAN_copy.pdf", index=1, content=shared),
        ]
        pipeline, engine, classifier = make_pipeline(
            settings,
            {"PAN.pdf": ocr()},
            {"PAN.pdf": classification(DocumentType.PAN, pan_number="ABCDE1234F")},
        )

        result = await pipeline.run(uploads, candidate_name="Ravi")

        assert engine.calls == ["PAN.pdf"]
        assert classifier.calls == ["PAN.pdf"]
        assert result.report.duplicate_uploads == 1

    async def test_the_duplicate_is_reported_and_left_out_of_the_zip(
        self, settings: Settings
    ) -> None:
        shared = PDF_BYTES + b"%identical\n"
        uploads = [
            upload("PAN.pdf", index=0, content=shared),
            upload("PAN_copy.pdf", index=1, content=shared),
        ]
        pipeline, _, _ = make_pipeline(
            settings,
            {"PAN.pdf": ocr()},
            {"PAN.pdf": classification(DocumentType.PAN, pan_number="ABCDE1234F")},
        )

        result = await pipeline.run(uploads, candidate_name="Ravi", request_id=REQUEST_ID)

        with zipfile.ZipFile(result.archive) as zf:
            names = [n for n in zf.namelist() if n != "report.json"]
        assert names == [f"01_Ravi_PAN_ABCDE1234F_{ID_TAG}.pdf"]

        duplicate = next(f for f in result.report.files if f.original_filename == "PAN_copy.pdf")
        assert duplicate.duplicate_of == "PAN.pdf"
        assert duplicate.output_filename is None
        assert duplicate.error is None, "a duplicate is not a failure"

    async def test_the_earliest_upload_is_the_one_kept(self, settings: Settings) -> None:
        """HR reconciles against the order they attached files in."""
        shared = PDF_BYTES + b"%identical\n"
        uploads = [
            upload("second.pdf", index=1, content=shared),
            upload("first.pdf", index=0, content=shared),
        ]
        pipeline, _, _ = make_pipeline(
            settings, {"first.pdf": ocr()}, {"first.pdf": classification(DocumentType.RESUME)}
        )

        result = await pipeline.run(uploads, candidate_name="Ravi")

        kept = next(f for f in result.report.files if f.duplicate_of is None)
        assert kept.original_filename == "first.pdf"

    async def test_a_duplicate_does_not_count_as_a_repeated_document_type(
        self, settings: Settings
    ) -> None:
        """Otherwise re-attaching one PAN looks like two PANs on file."""
        shared = PDF_BYTES + b"%identical\n"
        uploads = [
            upload("PAN.pdf", index=0, content=shared),
            upload("PAN_again.pdf", index=1, content=shared),
        ]
        pipeline, _, _ = make_pipeline(
            settings,
            {"PAN.pdf": ocr()},
            {"PAN.pdf": classification(DocumentType.PAN, pan_number="ABCDE1234F")},
        )

        result = await pipeline.run(uploads, candidate_name="Ravi")

        assert result.report.duplicate_document_types == []

    async def test_similar_but_not_identical_files_both_process(self, settings: Settings) -> None:
        """Two separate scans of one PAN card differ in every pixel. Byte equality
        must not be mistaken for a semantic judgement about content."""
        uploads = [upload("scan1.pdf", index=0), upload("scan2.pdf", index=1)]
        pipeline, engine, _ = make_pipeline(
            settings,
            {"scan1.pdf": ocr(), "scan2.pdf": ocr()},
            {
                "scan1.pdf": classification(DocumentType.PAN, pan_number="ABCDE1234F"),
                "scan2.pdf": classification(DocumentType.PAN, pan_number="ABCDE1234F"),
            },
        )

        result = await pipeline.run(uploads, candidate_name="Ravi")

        assert sorted(engine.calls) == ["scan1.pdf", "scan2.pdf"]
        assert result.report.duplicate_uploads == 0


class TestIdentifierValidation:
    """A misread identifier must never reach a filename unchallenged."""

    async def test_a_repairable_pan_is_corrected_and_flagged(self, settings: Settings) -> None:
        uploads = [upload("pan.pdf", index=0)]
        pipeline, _, _ = make_pipeline(
            settings,
            {"pan.pdf": ocr()},
            # `I` misread for `1` -- the single commonest PAN OCR error.
            {"pan.pdf": classification(DocumentType.PAN, pan_number="ABCDEI234F")},
        )

        result = await pipeline.run(uploads, candidate_name="Ravi", request_id=REQUEST_ID)
        report = result.report

        assert report.files[0].output_filename == f"01_Ravi_PAN_ABCDE1234F_{ID_TAG}.pdf"
        assert any("Repaired" in w for w in report.files[0].warnings)
        assert report.identifier_warnings

    async def test_an_unrepairable_identifier_is_dropped_from_the_filename(
        self, settings: Settings
    ) -> None:
        """The file is still delivered -- it simply carries no unverifiable number."""
        uploads = [upload("pan.pdf", index=0)]
        pipeline, _, _ = make_pipeline(
            settings,
            {"pan.pdf": ocr()},
            {"pan.pdf": classification(DocumentType.PAN, pan_number="NOTAPAN")},
        )

        result = await pipeline.run(uploads, candidate_name="Ravi", request_id=REQUEST_ID)

        assert result.report.files[0].output_filename == f"01_Ravi_PAN_{ID_TAG}.pdf"
        assert result.report.files[0].error is None

    async def test_a_degenerate_model_reading_is_not_dumped_whole_into_the_warning(
        self, settings: Settings
    ) -> None:
        """Regression test for a real production failure.

        Constrained decoding on a field the model found nothing for occasionally
        produces a long repeated phrase instead of an empty string -- observed
        live as ifsc_code coming back as "not present in document text. Leaving
        empty." repeated to several thousand characters. Validation already
        rejects it correctly (it is nowhere near 11 characters); this test is
        about the *display* of that rejection, which used to embed the entire
        raw value verbatim and turn one bad field into an unreadable wall of
        text in the report and the UI.
        """
        degenerate = "not present in document text. Leaving empty. - " * 140
        assert len(degenerate) > 5000, "the fixture must reproduce the same order of magnitude"

        uploads = [upload("payslip.pdf", index=0)]
        pipeline, _, _ = make_pipeline(
            settings,
            {"payslip.pdf": ocr()},
            {
                "payslip.pdf": classification(
                    DocumentType.SALARY_SLIP, full_name="Ravi", ifsc_code=degenerate
                )
            },
        )

        result = await pipeline.run(uploads)
        warning = next(w for w in result.report.files[0].warnings if w.startswith("Discarded"))

        assert len(warning) < 200, f"warning was {len(warning)} chars: leaked the raw value"
        assert degenerate not in warning

    async def test_the_rejected_reading_survives_in_the_warning(self, settings: Settings) -> None:
        """HR needs the bad value to find the page in the original scan."""
        uploads = [upload("pan.pdf", index=0)]
        pipeline, _, _ = make_pipeline(
            settings,
            {"pan.pdf": ocr()},
            {"pan.pdf": classification(DocumentType.PAN, pan_number="NOTAPAN")},
        )

        result = await pipeline.run(uploads, candidate_name="Ravi")

        assert any("NOTAPAN" in w for w in result.report.files[0].warnings)

    async def test_an_aadhaar_failing_its_checksum_is_rejected(self, settings: Settings) -> None:
        """A number that clears length and prefix and fails only on the checksum.

        Corrupting the check digit of a valid number isolates the Verhoeff branch;
        something like `123456789012` would be rejected by the leading-digit rule
        first and never reach it.
        """
        corrupted = VALID_AADHAAR[:-1] + str((int(VALID_AADHAAR[-1]) + 1) % 10)
        uploads = [upload("id.pdf", index=0)]
        pipeline, _, _ = make_pipeline(
            settings,
            {"id.pdf": ocr()},
            {
                "id.pdf": classification(
                    DocumentType.AADHAAR, aadhaar_number=corrupted, full_name="Ravi"
                )
            },
        )

        result = await pipeline.run(uploads, request_id=REQUEST_ID)

        assert result.report.files[0].output_filename == f"01_Ravi_Aadhaar_{ID_TAG}.pdf"
        assert any("checksum" in w.lower() for w in result.report.files[0].warnings)

    async def test_a_discarded_identifier_forces_human_review(self, settings: Settings) -> None:
        uploads = [upload("pan.pdf", index=0)]
        pipeline, _, _ = make_pipeline(
            settings,
            {"pan.pdf": ocr()},
            {"pan.pdf": classification(DocumentType.PAN, pan_number="NOTAPAN")},
        )

        result = await pipeline.run(uploads, candidate_name="Ravi", request_id=REQUEST_ID)

        assert result.report.needs_review == [f"01_Ravi_PAN_{ID_TAG}.pdf"]

    async def test_a_valid_identifier_passes_through_silently(self, settings: Settings) -> None:
        """No warning noise on the overwhelmingly common clean case."""
        uploads = [upload("pan.pdf", index=0)]
        pipeline, _, _ = make_pipeline(
            settings,
            {"pan.pdf": ocr()},
            {"pan.pdf": classification(DocumentType.PAN, pan_number="ABCDE1234F")},
        )

        result = await pipeline.run(uploads, candidate_name="Ravi")

        assert result.report.files[0].warnings == []
        assert result.report.identifier_warnings == []


class TestNameMismatch:
    async def test_another_persons_document_is_flagged(self, settings: Settings) -> None:
        """The weighted vote makes this document lose, which is right for naming
        and wrong for review -- the loser is exactly what needs a second look."""
        uploads = [
            upload("pan.pdf", index=0),
            upload("aadhaar.pdf", index=1),
            upload("stray.pdf", index=2),
        ]
        pipeline, _, _ = make_pipeline(
            settings,
            {u.original_filename: ocr() for u in uploads},
            {
                "pan.pdf": classification(
                    DocumentType.PAN, full_name="Ajay Kanagaraj", pan_number="ABCDE1234F"
                ),
                "aadhaar.pdf": classification(
                    DocumentType.AADHAAR,
                    full_name="Ajay Kanagaraj",
                    aadhaar_number=VALID_AADHAAR,
                ),
                "stray.pdf": classification(DocumentType.RESUME, full_name="Priya Sharma"),
            },
        )

        result = await pipeline.run(uploads)

        assert len(result.report.name_mismatches) == 1
        assert "stray.pdf" in result.report.name_mismatches[0]
        assert "Priya Sharma" in result.report.name_mismatches[0]

    async def test_an_abbreviated_name_is_not_flagged(self, settings: Settings) -> None:
        """`AJAY K` on a PAN against `AJAY KANAGARAJ` on an Aadhaar is routine."""
        uploads = [upload("pan.pdf", index=0), upload("aadhaar.pdf", index=1)]
        pipeline, _, _ = make_pipeline(
            settings,
            {u.original_filename: ocr() for u in uploads},
            {
                "pan.pdf": classification(
                    DocumentType.PAN, full_name="Ajay K", pan_number="ABCDE1234F"
                ),
                "aadhaar.pdf": classification(
                    DocumentType.AADHAAR,
                    full_name="Ajay Kanagaraj",
                    aadhaar_number=VALID_AADHAAR,
                ),
            },
        )

        result = await pipeline.run(uploads)

        assert result.report.name_mismatches == []

    async def test_a_mismatch_sends_the_file_to_review(self, settings: Settings) -> None:
        uploads = [upload("pan.pdf", index=0), upload("stray.pdf", index=1)]
        pipeline, _, _ = make_pipeline(
            settings,
            {u.original_filename: ocr() for u in uploads},
            {
                "pan.pdf": classification(
                    DocumentType.PAN, full_name="Ajay Kanagaraj", pan_number="ABCDE1234F"
                ),
                "stray.pdf": classification(DocumentType.RESUME, full_name="Priya Sharma"),
            },
        )

        result = await pipeline.run(uploads, candidate_name="Ajay Kanagaraj")
        stray = next(f for f in result.report.files if f.original_filename == "stray.pdf")

        assert stray.output_filename in result.report.needs_review

    async def test_the_mismatched_file_is_still_delivered(self, settings: Settings) -> None:
        """Flagged, not withheld. HR decides; the system does not silently drop."""
        uploads = [upload("pan.pdf", index=0), upload("stray.pdf", index=1)]
        pipeline, _, _ = make_pipeline(
            settings,
            {u.original_filename: ocr() for u in uploads},
            {
                "pan.pdf": classification(
                    DocumentType.PAN, full_name="Ajay Kanagaraj", pan_number="ABCDE1234F"
                ),
                "stray.pdf": classification(DocumentType.RESUME, full_name="Priya Sharma"),
            },
        )

        result = await pipeline.run(uploads, candidate_name="Ajay Kanagaraj")

        with zipfile.ZipFile(result.archive) as zf:
            names = [n for n in zf.namelist() if n != "report.json"]
        assert len(names) == 2


class TestCompleteness:
    async def test_missing_required_documents_are_reported(self, settings: Settings) -> None:
        uploads = [upload("cv.pdf", index=0)]
        pipeline, _, _ = make_pipeline(
            settings, {"cv.pdf": ocr()}, {"cv.pdf": classification(DocumentType.RESUME)}
        )

        result = await pipeline.run(
            uploads, required_types=(DocumentType.RESUME, DocumentType.PAN, DocumentType.AADHAAR)
        )

        assert result.report.missing_document_types == [DocumentType.PAN, DocumentType.AADHAAR]

    async def test_unexpected_duplicates_are_flagged(self, settings: Settings) -> None:
        uploads = [upload("a.pdf", index=0), upload("b.pdf", index=1)]
        pipeline, _, _ = make_pipeline(
            settings,
            {"a.pdf": ocr(), "b.pdf": ocr()},
            {
                "a.pdf": classification(DocumentType.PAN, pan_number="ABCDE1234F"),
                "b.pdf": classification(DocumentType.PAN, pan_number="ABCDE1234F"),
            },
        )

        result = await pipeline.run(uploads, required_types=())

        assert result.report.duplicate_document_types == [DocumentType.PAN]

    async def test_multiple_payslips_are_not_flagged_as_duplicates(
        self, settings: Settings
    ) -> None:
        uploads = [upload("a.pdf", index=0), upload("b.pdf", index=1)]
        pipeline, _, _ = make_pipeline(
            settings,
            {"a.pdf": ocr(), "b.pdf": ocr()},
            {
                "a.pdf": classification(
                    DocumentType.SALARY_SLIP, pay_period_year=2024, pay_period_month=1
                ),
                "b.pdf": classification(
                    DocumentType.SALARY_SLIP, pay_period_year=2024, pay_period_month=2
                ),
            },
        )

        result = await pipeline.run(uploads, required_types=())

        assert result.report.duplicate_document_types == []


class TestPrivacy:
    async def test_aadhaar_is_masked_in_the_filename_and_the_report(
        self, settings: Settings
    ) -> None:
        uploads = [upload("id.pdf", index=0)]
        pipeline, _, _ = make_pipeline(
            settings,
            {"id.pdf": ocr()},
            {
                "id.pdf": classification(
                    DocumentType.AADHAAR, aadhaar_number=VALID_AADHAAR, full_name="Ravi"
                )
            },
        )

        result = await pipeline.run(uploads, request_id=REQUEST_ID)
        report = read_report(result.archive)
        serialised = report.model_dump_json()

        assert VALID_AADHAAR not in serialised
        assert report.files[0].output_filename == (
            f"01_Ravi_Aadhaar_XXXXXXXX{VALID_AADHAAR[-4:]}_{ID_TAG}.pdf"
        )

    async def test_masking_can_be_disabled(self, settings: Settings) -> None:
        settings = settings.model_copy(update={"mask_sensitive_ids": False})
        uploads = [upload("id.pdf", index=0)]
        pipeline, _, _ = make_pipeline(
            settings,
            {"id.pdf": ocr()},
            {
                "id.pdf": classification(
                    DocumentType.AADHAAR, aadhaar_number=VALID_AADHAAR, full_name="Ravi"
                )
            },
        )

        result = await pipeline.run(uploads, request_id=REQUEST_ID)

        assert (
            result.report.files[0].output_filename
            == f"01_Ravi_Aadhaar_{VALID_AADHAAR}_{ID_TAG}.pdf"
        )


class TestConcurrency:
    async def test_every_file_is_processed_once(self, settings: Settings) -> None:
        uploads = [upload(f"f{i}.pdf", index=i) for i in range(5)]
        pipeline, engine, classifier = make_pipeline(
            settings,
            {u.original_filename: ocr() for u in uploads},
            {u.original_filename: classification(DocumentType.OTHER) for u in uploads},
        )

        await pipeline.run(uploads)

        assert sorted(engine.calls) == sorted(u.original_filename for u in uploads)
        assert len(classifier.calls) == 5


class TestTruncation:
    async def test_oversized_text_is_truncated_and_flagged(self, settings: Settings) -> None:
        settings = settings.model_copy(update={"ocr_text_char_budget": 100})
        uploads = [upload("long.pdf", index=0)]
        pipeline, _, _ = make_pipeline(
            settings,
            {"long.pdf": ocr(text="x" * 5000)},
            {"long.pdf": classification(DocumentType.BANK_STATEMENT)},
        )

        result = await pipeline.run(uploads)

        assert any("truncated" in w for w in result.report.files[0].warnings)
