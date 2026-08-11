from __future__ import annotations

import base64
from collections.abc import Iterator

import pytest

from app.config import Settings
from app.domain.schemas import DocumentClassification, ExtractedFields
from app.domain.validation import verhoeff_check_digit
from app.errors import OcrError
from app.services.ocr import OcrResult

# A real 1x1 PNG and a minimal PDF, so magic-byte sniffing exercises the same code
# path it will in production rather than being stubbed out.
PNG_BYTES = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
)
PDF_BYTES = b"%PDF-1.4\n1 0 obj<</Type/Catalog>>endobj\ntrailer<</Root 1 0 R>>\n%%EOF\n"

#: An Aadhaar-shaped number that passes the Verhoeff check, built from the
#: algorithm rather than taken from a real card. Fixtures need a *valid* number
#: because identifier validation now discards ones that fail the checksum -- an
#: arbitrary twelve digits would silently vanish from every filename it appears in.
_AADHAAR_PREFIX = "23412341234"
VALID_AADHAAR = _AADHAAR_PREFIX + str(verhoeff_check_digit(_AADHAAR_PREFIX))


@pytest.fixture
def settings() -> Settings:
    return Settings(
        environment="local",
        gcp_project_id="",
        docai_processor_id="",
        api_tokens=[],
        max_files_per_request=5,
        max_file_bytes=1024 * 1024,
        max_total_bytes=4 * 1024 * 1024,
        min_classification_confidence=0.55,
    )


class FakeOcrEngine:
    """Returns canned OCR per filename; unknown filenames raise, so the
    partial-failure path is exercised."""

    name = "fake-ocr"

    def __init__(self, results: dict[str, OcrResult] | None = None) -> None:
        self.results = results or {}
        self.calls: list[str] = []

    async def extract(self, *, content: bytes, mime_type: str, filename: str) -> OcrResult:
        del content, mime_type
        self.calls.append(filename)
        if filename not in self.results:
            raise OcrError(f"OCR failed for '{filename}'.")
        return self.results[filename]

    async def aclose(self) -> None:
        return None


class FakeClassifier:
    name = "fake-classifier"

    def __init__(self, results: dict[str, DocumentClassification] | None = None) -> None:
        self.results = results or {}
        self.calls: list[str] = []

    async def classify(
        self, *, ocr: OcrResult, filename: str, content_type: str
    ) -> DocumentClassification:
        del ocr, content_type
        self.calls.append(filename)
        return self.results.get(
            filename,
            DocumentClassification(
                document_type="Unknown", confidence=0.0, fields=ExtractedFields()
            ),
        )


@pytest.fixture
def ocr_text() -> Iterator[str]:
    yield "Permanent Account Number ABCDE1234F RAVI KUMAR"
