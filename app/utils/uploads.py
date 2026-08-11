"""Upload validation and bounded in-memory reads.

Uploads are never written to disk by this service. Files are read into memory
under a hard cap and discarded when the response is sent, which is what makes the
"no persistent storage" guarantee actually hold rather than depend on cleanup
code running.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass

import filetype
from fastapi import UploadFile

from app.errors import PayloadTooLargeError, UnsupportedMediaTypeError, ValidationError

#: MIME types accepted by the Document AI OCR processor for synchronous requests.
SUPPORTED_MIME_TYPES: frozenset[str] = frozenset(
    {
        "application/pdf",
        "image/jpeg",
        "image/png",
        "image/gif",
        "image/tiff",
        "image/bmp",
        "image/webp",
    }
)

_CHUNK_SIZE = 64 * 1024


@dataclass(frozen=True, slots=True)
class LoadedUpload:
    """An upload held entirely in memory, with a *sniffed* content type."""

    original_filename: str
    content_type: str
    content: bytes
    upload_index: int
    content_sha256: str
    """Digest of the bytes, used to spot the same file uploaded twice.

    Computed on load rather than lazily: the content is already in memory and in
    cache here, and it must be known before the batch fans out to OCR for the
    duplicate to be skipped rather than merely reported after the money is spent.
    """

    @property
    def size_bytes(self) -> int:
        return len(self.content)


def sniff_content_type(content: bytes, declared: str | None) -> str:
    """Determine the real content type from magic bytes.

    The browser-declared ``Content-Type`` is attacker-controlled and frequently
    wrong (``application/octet-stream`` for anything unfamiliar), so the sniffed
    value wins whenever we can determine one.
    """
    kind = filetype.guess(content)
    if kind is not None:
        return str(kind.mime)
    return (declared or "application/octet-stream").split(";")[0].strip().lower()


async def load_upload(
    upload: UploadFile,
    *,
    upload_index: int,
    max_file_bytes: int,
    remaining_budget: int,
) -> LoadedUpload:
    """Read one upload into memory, enforcing per-file and batch-wide caps.

    Reading in chunks means an oversized upload is rejected after ``max_file_bytes``
    rather than after the whole body has been buffered.
    """
    filename = (upload.filename or "").strip()
    if not filename:
        raise ValidationError("An uploaded file is missing a filename.")

    limit = min(max_file_bytes, remaining_budget)
    chunks: list[bytes] = []
    total = 0
    while chunk := await upload.read(_CHUNK_SIZE):
        total += len(chunk)
        if total > limit:
            reason = (
                f"'{filename}' exceeds the {max_file_bytes // (1024 * 1024)} MB per-file limit."
                if limit == max_file_bytes
                else "The batch exceeds the total upload size limit."
            )
            raise PayloadTooLargeError(reason)
        chunks.append(chunk)

    content = b"".join(chunks)
    if not content:
        raise ValidationError(f"'{filename}' is empty.")

    content_type = sniff_content_type(content, upload.content_type)
    if content_type not in SUPPORTED_MIME_TYPES:
        raise UnsupportedMediaTypeError(
            f"'{filename}' is a {content_type} file, which OCR cannot read. "
            "Supported formats: PDF, JPEG, PNG, GIF, TIFF, BMP, WEBP."
        )

    return LoadedUpload(
        original_filename=filename,
        content_type=content_type,
        content=content,
        upload_index=upload_index,
        content_sha256=hashlib.sha256(content).hexdigest(),
    )
