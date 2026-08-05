from __future__ import annotations

import pytest

from app.config import Settings
from app.domain.document_types import DocumentType, coerce_document_type
from app.services.classifier import (
    GeminiDocumentClassifier,
    StubDocumentClassifier,
    _photograph_heuristic,
    truncate_ocr_text,
)
from app.services.ocr import OcrResult


def test_gemini_classifier_constructs_its_chain() -> None:
    """Build the real classifier, not just the stub.

    Wiring the model to a structured-output schema is where an incompatibility
    surfaces, and it happens in `__init__` rather than at call time. Every other
    test here uses the stub, so nothing else would notice a schema the model's
    constrained-decoding mode rejects.
    """
    settings = Settings(gemini_api_key="AIzaNotARealKey", gemini_model="gemini-2.5-flash")

    classifier = GeminiDocumentClassifier(settings)

    assert classifier.name == "gemini"


class TestTruncateOcrText:
    def test_short_text_is_untouched(self) -> None:
        assert truncate_ocr_text("short", 100) == "short"

    def test_long_text_keeps_head_and_tail(self) -> None:
        text = "HEAD" + "x" * 5000 + "TAIL"
        truncated = truncate_ocr_text(text, 200)

        assert truncated.startswith("HEAD")
        assert truncated.endswith("TAIL")
        assert "omitted" in truncated

    def test_result_stays_near_the_budget(self) -> None:
        # The marker adds a bounded overhead; the point is that a 500 KB statement
        # cannot blow up the prompt.
        truncated = truncate_ocr_text("x" * 500_000, 1_000)
        assert len(truncated) < 1_200


class TestPhotographHeuristic:
    def test_an_image_with_no_text_is_a_photograph(self) -> None:
        result = _photograph_heuristic(
            ocr=OcrResult(text="   ", page_count=1, confidence=None),
            content_type="image/jpeg",
        )
        assert result is not None
        assert result.document_type == DocumentType.PHOTOGRAPH.value

    def test_it_is_flagged_for_review_rather_than_trusted(self) -> None:
        result = _photograph_heuristic(
            ocr=OcrResult(text="", page_count=1, confidence=None),
            content_type="image/png",
        )
        assert result is not None
        # Above min_classification_confidence (filed as a photograph) but below
        # review_confidence, so a badly-lit ID card scan still reaches a human.
        assert 0.55 < result.confidence < 0.75

    def test_an_image_with_text_goes_to_the_model(self) -> None:
        result = _photograph_heuristic(
            ocr=OcrResult(text="Permanent Account Number ABCDE1234F", page_count=1, confidence=0.9),
            content_type="image/jpeg",
        )
        assert result is None

    def test_a_pdf_always_goes_to_the_model(self) -> None:
        result = _photograph_heuristic(
            ocr=OcrResult(text="", page_count=1, confidence=None),
            content_type="application/pdf",
        )
        assert result is None


@pytest.mark.asyncio
class TestStubClassifier:
    async def test_returns_unknown_with_zero_confidence(self) -> None:
        result = await StubDocumentClassifier().classify(
            ocr=OcrResult(text="some text", page_count=1, confidence=0.9),
            filename="a.pdf",
            content_type="application/pdf",
        )
        assert result.document_type == DocumentType.UNKNOWN.value
        assert result.confidence == 0.0

    async def test_still_applies_the_photograph_heuristic(self) -> None:
        result = await StubDocumentClassifier().classify(
            ocr=OcrResult(text="", page_count=1, confidence=None),
            filename="a.png",
            content_type="image/png",
        )
        assert result.document_type == DocumentType.PHOTOGRAPH.value


class TestCoerceDocumentType:
    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            ("SalarySlip", DocumentType.SALARY_SLIP),
            ("salary_slip", DocumentType.SALARY_SLIP),
            ("salary slip", DocumentType.SALARY_SLIP),
            ("SALARY-SLIP", DocumentType.SALARY_SLIP),
            ("Aadhaar", DocumentType.AADHAAR),
            # A model that invents a type must degrade to Unknown, not explode.
            ("DrivingLicence", DocumentType.UNKNOWN),
            ("", DocumentType.UNKNOWN),
            (None, DocumentType.UNKNOWN),
        ],
    )
    def test_maps_model_output_onto_the_taxonomy(
        self, raw: str | None, expected: DocumentType
    ) -> None:
        assert coerce_document_type(raw) is expected
