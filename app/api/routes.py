"""HTTP endpoints."""

from __future__ import annotations

import json
from typing import Annotated

from fastapi import APIRouter, File, Form, Request, UploadFile
from fastapi.responses import StreamingResponse

from app import __version__
from app.api.deps import AuthDep, PipelineDep, SettingsDep
from app.domain import naming
from app.domain.document_types import (
    CANONICAL_ORDER,
    DEFAULT_REQUIRED_TYPES,
    DocumentType,
    coerce_document_type,
)
from app.domain.schemas import HealthResponse
from app.errors import PayloadTooLargeError, ValidationError
from app.logging_config import get_logger
from app.utils.uploads import SUPPORTED_MIME_TYPES, LoadedUpload, load_upload

logger = get_logger(__name__)

router = APIRouter()


@router.get("/health", response_model=HealthResponse, tags=["ops"])
async def health(request: Request, settings: SettingsDep) -> HealthResponse:
    """Liveness/readiness probe. Reports which engines are actually wired up."""
    return HealthResponse(
        status="ok",
        version=__version__,
        environment=settings.environment,
        ocr_engine=getattr(request.app.state.ocr_engine, "name", "unknown"),
        classifier=getattr(request.app.state.classifier, "name", "unknown"),
    )


@router.get("/api/v1/document-types", tags=["metadata"])
async def document_types() -> dict[str, object]:
    """Taxonomy and limits, so the frontend never hardcodes them."""
    return {
        "filing_order": [t.value for t in CANONICAL_ORDER],
        "default_required": [t.value for t in DEFAULT_REQUIRED_TYPES],
        "supported_mime_types": sorted(SUPPORTED_MIME_TYPES),
    }


@router.post(
    "/api/v1/documents/process",
    tags=["documents"],
    response_class=StreamingResponse,
    responses={
        200: {
            "content": {"application/zip": {}},
            "description": (
                "A ZIP containing the renamed documents in filing order plus "
                "`report.json` describing every input file."
            ),
        }
    },
)
async def process_documents(
    request: Request,
    _: AuthDep,
    settings: SettingsDep,
    pipeline: PipelineDep,
    files: Annotated[list[UploadFile], File(description="Candidate documents.")],
    candidate_name: Annotated[
        str | None,
        Form(description="Overrides the name inferred from the documents."),
    ] = None,
    pr_number: Annotated[
        str | None,
        Form(
            description=(
                "Optional HR requisition/PR number. When given, it replaces the "
                "opaque request id as the batch's tracking identifier in the ZIP "
                "filename and every file inside it."
            )
        ),
    ] = None,
    required_documents: Annotated[
        str | None,
        Form(description='JSON array of document types, e.g. ["Aadhaar","PAN"].'),
    ] = None,
) -> StreamingResponse:
    """Classify, rename and package one candidate's documents.

    Nothing is written to disk: uploads are held in memory for the life of the
    request and the ZIP is streamed straight back from a buffer.
    """
    if not files:
        raise ValidationError("Upload at least one file.")
    if len(files) > settings.max_files_per_request:
        raise PayloadTooLargeError(
            f"Too many files: {len(files)} uploaded, limit is {settings.max_files_per_request}."
        )

    uploads: list[LoadedUpload] = []
    remaining = settings.max_total_bytes
    for index, upload in enumerate(files):
        loaded = await load_upload(
            upload,
            upload_index=index,
            max_file_bytes=settings.max_file_bytes,
            remaining_budget=remaining,
        )
        remaining -= loaded.size_bytes
        uploads.append(loaded)

    result = await pipeline.run(
        uploads,
        candidate_name=candidate_name,
        required_types=_parse_required_documents(required_documents),
        request_id=getattr(request.state, "request_id", None),
        pr_number=pr_number,
    )

    tag = naming.sanitize_tag(pr_number) if pr_number and pr_number.strip() else None
    filename = f"documents_PR-{tag}.zip" if tag else f"documents_{result.request_id[:8]}.zip"
    return StreamingResponse(
        result.archive,
        media_type="application/zip",
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "Content-Length": str(result.archive.getbuffer().nbytes),
            "X-Request-Id": result.request_id,
            "X-Files-Failed": str(result.report.failed_count),
            "X-Needs-Review": str(len(result.report.needs_review)),
            # Nothing is cached: the response contains personal data.
            "Cache-Control": "no-store",
        },
    )


def _parse_required_documents(raw: str | None) -> tuple[DocumentType, ...]:
    if not raw or not raw.strip():
        return DEFAULT_REQUIRED_TYPES
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValidationError(
            "`required_documents` must be a JSON array of document type names."
        ) from exc
    if not isinstance(parsed, list):
        raise ValidationError("`required_documents` must be a JSON array.")

    types = tuple(coerce_document_type(str(item)) for item in parsed)
    unknown = [
        str(item)
        for item, resolved in zip(parsed, types, strict=True)
        if resolved is DocumentType.UNKNOWN and str(item).lower() != "unknown"
    ]
    if unknown:
        raise ValidationError(f"Unrecognised document types: {', '.join(unknown)}.")
    return types
