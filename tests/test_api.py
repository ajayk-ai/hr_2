from __future__ import annotations

import io
import json
import zipfile
from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr

from app.config import Settings
from app.domain.document_types import DocumentType
from app.main import create_app
from app.services.ocr import OcrResult
from tests.conftest import PDF_BYTES, PNG_BYTES, FakeClassifier, FakeOcrEngine
from tests.test_pipeline import classification


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch, settings: Settings) -> Iterator[TestClient]:
    """A client backed by fake engines, so no network call is ever made."""
    engine = FakeOcrEngine(
        {
            "cv.pdf": OcrResult(text="resume text", page_count=2, confidence=0.9),
            "photo.png": OcrResult(text="", page_count=1, confidence=None),
        }
    )
    classifier = FakeClassifier(
        {
            "cv.pdf": classification(DocumentType.RESUME, full_name="Ravi Kumar"),
            "photo.png": classification(DocumentType.PHOTOGRAPH),
        }
    )
    monkeypatch.setattr("app.main.build_ocr_engine", lambda _: engine)
    monkeypatch.setattr("app.main.build_classifier", lambda _: classifier)

    with TestClient(create_app(settings)) as test_client:
        yield test_client


def post_files(client: TestClient, files: list[tuple[str, bytes, str]], **data: str):
    return client.post(
        "/api/v1/documents/process",
        files=[("files", (name, content, ctype)) for name, content, ctype in files],
        data=data,
    )


class TestHealth:
    def test_reports_the_wired_engines(self, client: TestClient) -> None:
        body = client.get("/health").json()
        assert body["status"] == "ok"
        assert body["ocr_engine"] == "fake-ocr"
        assert body["classifier"] == "fake-classifier"


class TestDocumentTypes:
    def test_exposes_the_taxonomy(self, client: TestClient) -> None:
        body = client.get("/api/v1/document-types").json()
        assert body["filing_order"][0] == "Photograph"
        assert "application/pdf" in body["supported_mime_types"]


class TestProcess:
    def test_returns_a_zip_of_renamed_documents(self, client: TestClient) -> None:
        response = post_files(
            client,
            [("cv.pdf", PDF_BYTES, "application/pdf"), ("photo.png", PNG_BYTES, "image/png")],
        )

        assert response.status_code == 200
        assert response.headers["content-type"] == "application/zip"
        assert "attachment" in response.headers["content-disposition"]
        assert response.headers["cache-control"] == "no-store"

        with zipfile.ZipFile(io.BytesIO(response.content)) as zf:
            names = zf.namelist()
            report = json.loads(zf.read("report.json"))

        assert names == [
            "01_RaviKumar_Photograph.png",
            "02_RaviKumar_Resume.pdf",
            "report.json",
        ]
        assert report["candidate_name"] == "Ravi Kumar"
        assert report["request_id"] == response.headers["x-request-id"]

    def test_an_explicit_candidate_name_is_honoured(self, client: TestClient) -> None:
        response = post_files(
            client, [("cv.pdf", PDF_BYTES, "application/pdf")], candidate_name="Priya Nair"
        )
        with zipfile.ZipFile(io.BytesIO(response.content)) as zf:
            assert "01_PriyaNair_Resume.pdf" in zf.namelist()

    def test_missing_documents_are_reported(self, client: TestClient) -> None:
        response = post_files(
            client,
            [("cv.pdf", PDF_BYTES, "application/pdf")],
            required_documents='["Resume", "PAN"]',
        )
        with zipfile.ZipFile(io.BytesIO(response.content)) as zf:
            report = json.loads(zf.read("report.json"))
        assert report["missing_document_types"] == ["PAN"]

    def test_content_type_is_sniffed_not_trusted(self, client: TestClient) -> None:
        # Declared as a PDF, actually a PNG. The sniffed type must win, so the file
        # is stored with a .png extension.
        response = post_files(client, [("photo.png", PNG_BYTES, "application/pdf")])
        with zipfile.ZipFile(io.BytesIO(response.content)) as zf:
            assert any(n.endswith(".png") for n in zf.namelist())


class TestValidation:
    def test_rejects_an_empty_upload_list(self, client: TestClient) -> None:
        assert client.post("/api/v1/documents/process", files=[]).status_code == 422

    def test_rejects_unsupported_file_types(self, client: TestClient) -> None:
        response = post_files(client, [("virus.exe", b"MZ\x90\x00" * 40, "application/pdf")])
        assert response.status_code == 415
        assert response.json()["error"]["code"] == "unsupported_media_type"

    def test_rejects_empty_files(self, client: TestClient) -> None:
        response = post_files(client, [("empty.pdf", b"", "application/pdf")])
        assert response.status_code == 422

    def test_enforces_the_file_count_limit(self, client: TestClient) -> None:
        response = post_files(
            client, [(f"cv{i}.pdf", PDF_BYTES, "application/pdf") for i in range(6)]
        )
        assert response.status_code == 413
        assert response.json()["error"]["code"] == "payload_too_large"

    def test_enforces_the_per_file_size_limit(self, client: TestClient) -> None:
        oversized = PDF_BYTES + b"\x00" * (2 * 1024 * 1024)
        response = post_files(client, [("big.pdf", oversized, "application/pdf")])
        assert response.status_code == 413

    def test_rejects_malformed_required_documents(self, client: TestClient) -> None:
        response = post_files(
            client, [("cv.pdf", PDF_BYTES, "application/pdf")], required_documents="not json"
        )
        assert response.status_code == 422

    def test_rejects_unknown_required_document_types(self, client: TestClient) -> None:
        response = post_files(
            client,
            [("cv.pdf", PDF_BYTES, "application/pdf")],
            required_documents='["DrivingLicence"]',
        )
        assert response.status_code == 422
        assert "DrivingLicence" in response.json()["error"]["message"]


class TestAuth:
    @pytest.fixture
    def secured_client(
        self, monkeypatch: pytest.MonkeyPatch, settings: Settings
    ) -> Iterator[TestClient]:
        secured = settings.model_copy(update={"api_tokens": [SecretStr("s3cret")]})
        monkeypatch.setattr("app.main.build_ocr_engine", lambda _: FakeOcrEngine({}))
        monkeypatch.setattr("app.main.build_classifier", lambda _: FakeClassifier({}))
        with TestClient(create_app(secured)) as test_client:
            yield test_client

    def test_rejects_a_request_with_no_token(self, secured_client: TestClient) -> None:
        response = post_files(secured_client, [("cv.pdf", PDF_BYTES, "application/pdf")])
        assert response.status_code == 401

    def test_rejects_a_wrong_token(self, secured_client: TestClient) -> None:
        response = secured_client.post(
            "/api/v1/documents/process",
            files=[("files", ("cv.pdf", PDF_BYTES, "application/pdf"))],
            headers={"Authorization": "Bearer wrong"},
        )
        assert response.status_code == 401

    def test_accepts_the_configured_token(self, secured_client: TestClient) -> None:
        response = secured_client.post(
            "/api/v1/documents/process",
            files=[("files", ("cv.pdf", PDF_BYTES, "application/pdf"))],
            headers={"Authorization": "Bearer s3cret"},
        )
        assert response.status_code == 200

    def test_health_stays_open(self, secured_client: TestClient) -> None:
        assert secured_client.get("/health").status_code == 200
