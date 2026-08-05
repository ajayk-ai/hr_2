"""Document AI client construction and its failure modes.

Focused on what happens *around* the API call rather than the call itself: the
client is built lazily behind a lock, and getting that wrong turns one
misconfiguration into a per-document stall.
"""

from __future__ import annotations

import asyncio

import pytest
from google.api_core import exceptions as gcp_exceptions
from google.auth.exceptions import DefaultCredentialsError
from google.protobuf.field_mask_pb2 import FieldMask

from app.config import Settings
from app.services.ocr import DocumentAIOcrEngine, OcrError, _build_field_mask


@pytest.fixture
def settings() -> Settings:
    return Settings(
        gcp_project_id="hr-project-504507",
        docai_location="asia-south1",
        docai_processor_id="7682e85404771a77",
        ocr_concurrency=5,
    )


@pytest.mark.asyncio
async def test_missing_credentials_raise_actionable_error(
    settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    def _raise(**_: object) -> object:
        raise DefaultCredentialsError("Your default credentials were not found.")

    monkeypatch.setattr("app.services.ocr.documentai.DocumentProcessorServiceAsyncClient", _raise)
    engine = DocumentAIOcrEngine(settings)

    with pytest.raises(OcrError) as excinfo:
        await engine.extract(content=b"x", mime_type="image/png", filename="a.png")

    # The stock google-auth message names no environment variable.
    assert "GOOGLE_APPLICATION_CREDENTIALS" in str(excinfo.value)


@pytest.mark.asyncio
async def test_credential_failure_is_not_retried_per_document(
    settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The expensive part is the failure, not the success.

    google-auth probes the GCE metadata server before giving up, which costs
    seconds. Retrying that for every file serialises the batch behind the client
    lock and multiplies the wait by the file count.
    """
    attempts = 0

    def _raise(**_: object) -> object:
        nonlocal attempts
        attempts += 1
        raise DefaultCredentialsError("Your default credentials were not found.")

    monkeypatch.setattr("app.services.ocr.documentai.DocumentProcessorServiceAsyncClient", _raise)
    engine = DocumentAIOcrEngine(settings)

    for name in ("a.png", "b.png", "c.png"):
        with pytest.raises(OcrError):
            await engine.extract(content=b"x", mime_type="image/png", filename=name)

    assert attempts == 1, "credential discovery should be attempted once per process"


@pytest.mark.parametrize("include_quality_scores", [False, True])
def test_field_mask_is_accepted_by_protobuf(include_quality_scores: bool) -> None:
    """The mask must parse, or every request dies before reaching the network.

    protobuf's FieldMask rejects underscores, so the snake_case field names used
    everywhere else in this codebase are silently wrong here. Asserting on the
    parsed result rather than the string keeps this honest if the mask changes.
    """
    mask = FieldMask()
    mask.FromJsonString(_build_field_mask(include_quality_scores=include_quality_scores))

    assert "pages.page_number" in mask.paths
    assert "text" in mask.paths
    assert ("pages.image_quality_scores" in mask.paths) is include_quality_scores


def test_field_mask_addresses_only_direct_page_fields() -> None:
    """Document AI rejects masks that reach deeper than one level under `pages`.

    `pages.layout.confidence` returns a bare 400 with no usable explanation, so
    the constraint is pinned here rather than rediscovered against the live API.
    """
    for path in _build_field_mask(include_quality_scores=True).split(","):
        assert path.count(".") <= 1, f"{path} is nested too deeply for the API"


@pytest.mark.asyncio
async def test_billing_disabled_is_reported_as_such(
    settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    """FAILED_PRECONDITION is what a project without billing returns.

    Left to the generic handler it becomes a bare "OCR failed", which sends the
    reader looking at the document instead of at the Cloud console.
    """

    class _FakeClient:
        def __init__(self, **_: object) -> None: ...

        async def process_document(self, **_: object) -> object:
            raise gcp_exceptions.FailedPrecondition("This API method requires billing")

    monkeypatch.setattr(
        "app.services.ocr.documentai.DocumentProcessorServiceAsyncClient", _FakeClient
    )
    engine = DocumentAIOcrEngine(settings)

    with pytest.raises(OcrError) as excinfo:
        await engine.extract(content=b"x", mime_type="image/png", filename="a.png")

    assert "billing" in str(excinfo.value).lower()


@pytest.mark.asyncio
async def test_concurrent_documents_build_the_client_once(
    settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The double-checked lock must not let a burst create parallel channels."""
    built = 0

    class _FakeClient:
        def __init__(self, **_: object) -> None:
            nonlocal built
            built += 1

        async def process_document(self, **_: object) -> object:
            raise AssertionError("not reached in this test")

    monkeypatch.setattr(
        "app.services.ocr.documentai.DocumentProcessorServiceAsyncClient", _FakeClient
    )
    engine = DocumentAIOcrEngine(settings)

    clients = await asyncio.gather(*(engine._get_client() for _ in range(8)))

    assert built == 1
    assert len({id(client) for client in clients}) == 1
