"""Typed application errors.

Every error carries an HTTP status and a stable machine-readable ``code`` so the
frontend can branch on failures without string-matching prose.
"""

from __future__ import annotations

from http import HTTPStatus


class AppError(Exception):
    """Base class for errors that map onto an HTTP response."""

    status_code: int = HTTPStatus.INTERNAL_SERVER_ERROR
    code: str = "internal_error"

    def __init__(self, message: str, *, detail: str | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.detail = detail


class ValidationError(AppError):
    status_code = HTTPStatus.UNPROCESSABLE_ENTITY
    code = "validation_error"


class PayloadTooLargeError(AppError):
    status_code = HTTPStatus.REQUEST_ENTITY_TOO_LARGE
    code = "payload_too_large"


class UnsupportedMediaTypeError(AppError):
    status_code = HTTPStatus.UNSUPPORTED_MEDIA_TYPE
    code = "unsupported_media_type"


class AuthenticationError(AppError):
    status_code = HTTPStatus.UNAUTHORIZED
    code = "unauthenticated"


class OcrError(AppError):
    """OCR failed for a single document; the batch continues without it."""

    status_code = HTTPStatus.BAD_GATEWAY
    code = "ocr_failed"


class ClassificationError(AppError):
    """The LLM could not produce a usable classification for a document."""

    status_code = HTTPStatus.BAD_GATEWAY
    code = "classification_failed"


class ServiceUnavailableError(AppError):
    status_code = HTTPStatus.SERVICE_UNAVAILABLE
    code = "service_unavailable"
