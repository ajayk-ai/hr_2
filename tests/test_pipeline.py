from __future__ import annotations

import json
import zipfile

import pytest

from app.config import Settings
from app.domain.document_types import DocumentType
from app.domain.schemas import DocumentClassification, ExtractedFields, ProcessingReport
from app.services.ocr import OcrResult
from app.services.pipeline import DocumentPipeline
from app.utils.uploads import LoadedUpload
from tests.conftest import PDF_BYTES, PNG_BYTES, FakeClassifier, FakeOcrEngine

pytestmark = pytest.mark.asyncio


def upload(
    name: str, *, index: int, content: bytes = PDF_BYTES, ctype: str = "application/pdf"
) -> LoadedUpload:
    return LoadedUpload(
        original_filename=name, content_type=ctype, content=content, upload_index=index
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

        result = await pipeline.run(uploads)

        with zipfile.ZipFile(result.archive) as zf:
            names = [n for n in zf.namelist() if n != "report.json"]

        assert names == [
            "01_RaviKumar_Photograph.png",
            "02_RaviKumar_PAN_ABCDE1234F.pdf",
            "03_RaviKumar_Marksheet_BTech-2018.pdf",
        ]

    async def test_original_bytes_are_preserved_exactly(self, settings: Settings) -> None:
        uploads = [upload("cv.pdf", index=0)]
        pipeline, _, _ = make_pipeline(
            settings, {"cv.pdf": ocr()}, {"cv.pdf": classification(DocumentType.RESUME)}
        )

        result = await pipeline.run(uploads, candidate_name="Ravi Kumar")

        with zipfile.ZipFile(result.archive) as zf:
            assert zf.read("01_RaviKumar_Resume.pdf") == PDF_BYTES

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
                    aadhaar_number="123412341234",
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

        result = await pipeline.run(uploads)

        with zipfile.ZipFile(result.archive) as zf:
            assert [n for n in zf.namelist() if n != "report.json"] == ["01_Ravi_Resume.pdf"]

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

        result = await pipeline.run(uploads)

        assert "Low scan quality." in result.report.files[0].warnings
        assert result.report.needs_review == ["01_Ravi_Resume.pdf"]


class TestConfidenceGate:
    async def test_low_confidence_is_filed_as_unknown(self, settings: Settings) -> None:
        uploads = [upload("maybe.pdf", index=0)]
        pipeline, _, _ = make_pipeline(
            settings,
            {"maybe.pdf": ocr()},
            {"maybe.pdf": classification(DocumentType.AADHAAR, confidence=0.3, full_name="Ravi")},
        )

        result = await pipeline.run(uploads)
        report = result.report.files[0]

        assert report.document_type is DocumentType.UNKNOWN
        assert report.output_filename == "01_Ravi_Unknown.pdf"
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

        result = await pipeline.run(uploads)

        assert result.report.files[0].document_type is DocumentType.PAN
        assert result.report.needs_review == ["01_Ravi_PAN_ABCDE1234F.pdf"]

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
                    aadhaar_number="123412341234",
                    full_name="Ravi",
                )
            },
        )

        result = await pipeline.run(uploads)

        assert result.report.files[0].document_type is DocumentType.AADHAAR


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
                    DocumentType.AADHAAR, aadhaar_number="123412341234", full_name="Ravi"
                )
            },
        )

        result = await pipeline.run(uploads)
        report = read_report(result.archive)
        serialised = report.model_dump_json()

        assert "123412341234" not in serialised
        assert report.files[0].output_filename == "01_Ravi_Aadhaar_XXXXXXXX1234.pdf"

    async def test_masking_can_be_disabled(self, settings: Settings) -> None:
        settings = settings.model_copy(update={"mask_sensitive_ids": False})
        uploads = [upload("id.pdf", index=0)]
        pipeline, _, _ = make_pipeline(
            settings,
            {"id.pdf": ocr()},
            {
                "id.pdf": classification(
                    DocumentType.AADHAAR, aadhaar_number="123412341234", full_name="Ravi"
                )
            },
        )

        result = await pipeline.run(uploads)

        assert result.report.files[0].output_filename == "01_Ravi_Aadhaar_123412341234.pdf"


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
