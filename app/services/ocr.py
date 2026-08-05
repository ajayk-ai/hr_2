"""OCR backends.

The pipeline depends on the :class:`OcrEngine` protocol, not on Document AI, so
the engine can be swapped (a local Tesseract fallback, a stub for tests) without
the orchestration knowing.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol, runtime_checkable

from google.api_core import exceptions as gcp_exceptions
from google.api_core.client_options import ClientOptions
from google.auth.exceptions import DefaultCredentialsError
from google.cloud import documentai_v1 as documentai
from google.oauth2 import service_account
from tenacity import (
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)
from tenacity.asyncio import AsyncRetrying

from app.config import Settings
from app.errors import OcrError
from app.logging_config import get_logger

logger = get_logger(__name__)

#: Transient GCP failures worth retrying. Anything else (INVALID_ARGUMENT,
#: PERMISSION_DENIED) is a caller error and retrying only wastes the request.
_RETRYABLE = (
    gcp_exceptions.ServiceUnavailable,
    gcp_exceptions.DeadlineExceeded,
    gcp_exceptions.InternalServerError,
    gcp_exceptions.ResourceExhausted,
    gcp_exceptions.TooManyRequests,
    gcp_exceptions.Aborted,
)

#: Trimming the response to what we actually consume cuts deserialisation cost
#: substantially on multi-page PDFs.
#:
#: Paths must be camelCase: protobuf's FieldMask parser rejects underscores
#: outright, so a snake_case path raises ValueError while building the request,
#: before anything reaches the network.
#: Only top-level document fields and *direct* page fields are addressable, so
#: `pages.layout` is accepted where the narrower `pages.layout.confidence` is
#: not -- the API rejects the latter with an otherwise unexplained 400.
_BASE_FIELD_MASK = "text,entities,pages.pageNumber,pages.layout,pages.detectedLanguages"


def _build_field_mask(*, include_quality_scores: bool) -> str:
    if include_quality_scores:
        return f"{_BASE_FIELD_MASK},pages.imageQualityScores"
    return _BASE_FIELD_MASK


@dataclass(frozen=True, slots=True)
class OcrResult:
    text: str
    page_count: int
    confidence: float | None
    """Mean layout confidence across pages, or None when the engine reports none."""
    entities: dict[str, str] = field(default_factory=dict)
    warnings: tuple[str, ...] = ()


@runtime_checkable
class OcrEngine(Protocol):
    name: str

    async def extract(self, *, content: bytes, mime_type: str, filename: str) -> OcrResult: ...

    async def aclose(self) -> None: ...


class DocumentAIOcrEngine:
    """Google Document AI, called synchronously per document.

    Synchronous ``processDocument`` is capped at 15 pages for the OCR processor.
    Beyond that Google returns INVALID_ARGUMENT; we translate it into an
    actionable message rather than surfacing a gRPC error to HR.
    """

    name = "google-document-ai"

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._processor_path = settings.docai_processor_path
        self._semaphore = asyncio.Semaphore(settings.ocr_concurrency)
        self._client: documentai.DocumentProcessorServiceAsyncClient | None = None
        self._client_lock = asyncio.Lock()
        self._credentials_detail: str | None = None
        self._field_mask = _build_field_mask(
            include_quality_scores=settings.docai_image_quality_scores
        )

    def _load_credentials(self) -> service_account.Credentials | None:
        """Resolve the key file named in settings, or defer to ADC.

        Returning None hands authentication back to google-auth's default search,
        which is what runs on Cloud Run or after `gcloud auth application-default
        login`.
        """
        key_path = self._settings.google_application_credentials
        if not key_path:
            return None
        if not Path(key_path).is_file():
            # Worth its own message: an absent file and absent credentials produce
            # very different fixes, and google-auth reports neither clearly.
            raise OcrError(
                f"The service account key at '{key_path}' does not exist. Check "
                "GOOGLE_APPLICATION_CREDENTIALS.",
                detail="credentials file not found",
            )
        # google-auth ships no annotations for this constructor.
        credentials: service_account.Credentials = (
            service_account.Credentials.from_service_account_file(  # type: ignore[no-untyped-call]
                key_path
            )
        )
        return credentials

    @staticmethod
    def _credentials_error(detail: str) -> OcrError:
        return OcrError(
            "Google credentials were not found. Set GOOGLE_APPLICATION_CREDENTIALS "
            "to a service account key file, or run "
            "`gcloud auth application-default login`.",
            detail=detail,
        )

    async def _get_client(self) -> documentai.DocumentProcessorServiceAsyncClient:
        # Built on first use and shared: the gRPC channel is expensive to create
        # and safe to reuse across concurrent requests. Plainly locked rather than
        # double-checked -- construction is the expensive part, an uncontended
        # asyncio.Lock is not, and these calls are network-bound regardless.
        async with self._client_lock:
            if self._client is not None:
                return self._client
            # Absent credentials are a deployment mistake, not a transient fault.
            # Re-attempting per document would serialise the whole upload behind
            # this lock, each file paying another multi-second metadata-server
            # probe that cannot succeed -- 25 files became five minutes of waiting
            # before returning the same error 25 times.
            if self._credentials_detail is not None:
                raise self._credentials_error(self._credentials_detail)
            try:
                client = documentai.DocumentProcessorServiceAsyncClient(
                    credentials=self._load_credentials(),
                    client_options=ClientOptions(api_endpoint=self._settings.docai_api_endpoint),
                )
            except DefaultCredentialsError as exc:
                # Credential discovery happens at construction rather than at the
                # API call, so this escapes the handlers around `process_document`,
                # and the stock message does not say which knob to turn.
                self._credentials_detail = str(exc)
                raise self._credentials_error(self._credentials_detail) from exc
            self._client = client
        return client

    async def extract(self, *, content: bytes, mime_type: str, filename: str) -> OcrResult:
        client = await self._get_client()
        request = documentai.ProcessRequest(
            name=self._processor_path,
            raw_document=documentai.RawDocument(content=content, mime_type=mime_type),
            process_options=documentai.ProcessOptions(
                ocr_config=documentai.OcrConfig(
                    # Uses the PDF's embedded text layer when there is one: faster
                    # and more accurate than rasterising and re-recognising it.
                    enable_native_pdf_parsing=True,
                    enable_image_quality_scores=self._settings.docai_image_quality_scores,
                )
            ),
            # We never render the document, so returning page images would be
            # megabytes of wasted payload per request.
            imageless_mode=True,
            field_mask=self._field_mask,
        )

        async with self._semaphore:
            try:
                async for attempt in AsyncRetrying(
                    stop=stop_after_attempt(3),
                    wait=wait_exponential(multiplier=0.5, max=8),
                    retry=retry_if_exception_type(_RETRYABLE),
                    reraise=True,
                ):
                    with attempt:
                        response = await client.process_document(
                            request=request,
                            timeout=self._settings.docai_timeout_seconds,
                        )
            except gcp_exceptions.InvalidArgument as exc:
                # Page count is the likeliest cause but far from the only one, and
                # asserting it outright sent one debugging session looking at a
                # single-page PNG for a limit it could not possibly have hit.
                # Google's own message goes first; the hint follows it.
                raise OcrError(
                    f"Document AI rejected '{filename}': {exc.message} Synchronous "
                    "processing is limited to 15 pages -- split larger PDFs.",
                    detail=exc.message,
                ) from exc
            except gcp_exceptions.PermissionDenied as exc:
                raise OcrError(
                    "Document AI denied the request. Check the service account's "
                    "Document AI User role and the processor id.",
                    detail=exc.message,
                ) from exc
            except gcp_exceptions.FailedPrecondition as exc:
                # Overwhelmingly this is billing not being enabled on the project,
                # which every new project hits before its first successful call.
                # The condition is account-wide, so it will fail identically for
                # every document until someone acts on it.
                raise OcrError(
                    "Document AI could not run. This usually means billing is not "
                    "enabled for the Google Cloud project; enable it at "
                    "https://console.cloud.google.com/billing and retry.",
                    detail=exc.message,
                ) from exc
            except gcp_exceptions.GoogleAPICallError as exc:
                raise OcrError(f"OCR failed for '{filename}'.", detail=exc.message) from exc

        return _to_ocr_result(response.document)

    async def aclose(self) -> None:
        if self._client is not None:
            await self._client.transport.close()  # type: ignore[no-untyped-call]
            self._client = None


def _to_ocr_result(document: documentai.Document) -> OcrResult:
    confidences = [
        page.layout.confidence
        for page in document.pages
        if page.layout is not None and page.layout.confidence
    ]
    warnings: list[str] = []

    poor_quality_pages = [
        page.page_number
        for page in document.pages
        if page.image_quality_scores is not None
        and page.image_quality_scores.quality_score
        and page.image_quality_scores.quality_score < 0.5
    ]
    if poor_quality_pages:
        warnings.append(
            "Low scan quality on page(s) "
            + ", ".join(str(p) for p in poor_quality_pages)
            + "; extracted fields may be unreliable."
        )
    if not document.text.strip():
        warnings.append("No text layer was recovered from this file.")

    entities = {
        entity.type_: entity.mention_text
        for entity in document.entities
        if entity.type_ and entity.mention_text
    }

    return OcrResult(
        text=document.text,
        page_count=len(document.pages),
        confidence=sum(confidences) / len(confidences) if confidences else None,
        entities=entities,
        warnings=tuple(warnings),
    )


class StubOcrEngine:
    """No-op engine used when Document AI is not configured.

    Lets the frontend and the rest of the pipeline be exercised locally without
    GCP credentials. It reports its own uselessness through a warning so a
    stub-backed result is never mistaken for a real one.
    """

    name = "stub"

    async def extract(self, *, content: bytes, mime_type: str, filename: str) -> OcrResult:
        del content, mime_type
        logger.warning("ocr.stub_engine_used", filename=filename)
        return OcrResult(
            text="",
            page_count=0,
            confidence=None,
            warnings=("OCR is not configured; no text was extracted.",),
        )

    async def aclose(self) -> None:
        return None


def build_ocr_engine(settings: Settings) -> OcrEngine:
    if settings.docai_configured:
        return DocumentAIOcrEngine(settings)
    return StubOcrEngine()
