"""Application settings, loaded from the environment (or a local `.env`).

Settings are read once at import time via :func:`get_settings` and cached, so
they can be depended on cheaply from request handlers.
"""

from __future__ import annotations

import json
from functools import lru_cache
from typing import Annotated, Literal

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

MEGABYTE = 1024 * 1024


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        env_prefix="HRDOC_",
        extra="ignore",
    )

    # ---- Runtime -----------------------------------------------------------
    environment: Literal["local", "staging", "production"] = "local"
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"
    log_json: bool = Field(
        default=False,
        description="Emit structured JSON logs. Enable in deployed environments.",
    )
    # `NoDecode` on the list fields suppresses pydantic-settings' automatic JSON
    # decoding of complex types, which happens inside the env source *before* any
    # validator runs -- without it, `_split_csv` below is dead code and a plain
    # `HRDOC_CORS_ORIGINS=http://localhost:5173` raises a JSONDecodeError at boot.
    cors_origins: Annotated[list[str], NoDecode] = Field(
        default_factory=lambda: ["http://localhost:5173"]
    )
    use_system_trust_store: bool = Field(
        default=True,
        description=(
            "Verify TLS against the OS certificate store rather than certifi's "
            "bundle. Required behind TLS-intercepting proxies, whose private CA "
            "certifi does not carry; harmless elsewhere, since the system store "
            "holds the same public roots."
        ),
    )

    # ---- Server --------------------------------------------------------------
    host: str = Field(
        default="0.0.0.0",
        description="Interface uvicorn binds to when started via `python -m app.main`.",
    )
    port: Annotated[int, Field(gt=0, le=65535)] = 8000

    # ---- Auth --------------------------------------------------------------
    api_tokens: Annotated[list[SecretStr], NoDecode] = Field(
        default_factory=list,
        description=(
            "Bearer tokens accepted by the upload endpoints. When empty, auth is "
            "disabled -- allowed only outside production."
        ),
    )

    # ---- Upload limits -----------------------------------------------------
    max_files_per_request: Annotated[int, Field(gt=0, le=100)] = 25
    max_file_bytes: Annotated[int, Field(gt=0)] = 20 * MEGABYTE
    max_total_bytes: Annotated[int, Field(gt=0)] = 100 * MEGABYTE

    # ---- Google Document AI ------------------------------------------------
    gcp_project_id: str = ""
    docai_location: str = Field(
        default="us",
        description="Document AI multi-region: 'us' or 'eu' (or a specific region).",
    )
    docai_processor_id: str = ""
    docai_processor_version: str = Field(
        default="",
        description="Optional pinned processor version id. Empty uses the default version.",
    )
    docai_timeout_seconds: float = 60.0
    ocr_concurrency: Annotated[int, Field(gt=0, le=32)] = 5
    docai_image_quality_scores: bool = Field(
        default=False,
        description=(
            "Request per-page image quality scores, used to warn about unreadable "
            "scans. Off by default: only some processor versions support it, and "
            "the rest reject the whole request with INVALID_ARGUMENT."
        ),
    )
    # Read under its conventional unprefixed name so a value in `.env` behaves the
    # way people expect. google-auth only ever consults the real process
    # environment, so a `.env` entry alone is invisible to it -- the OCR engine
    # loads this path and passes the credentials to the client explicitly.
    # Left empty, authentication falls back to Application Default Credentials.
    google_application_credentials: str = Field(
        default="",
        validation_alias="GOOGLE_APPLICATION_CREDENTIALS",
    )

    # ---- Gemini ------------------------------------------------------------
    gemini_api_key: SecretStr = SecretStr("")
    gemini_model: str = "gemini-2.5-flash"
    gemini_temperature: Annotated[float, Field(ge=0.0, le=2.0)] = 0.0
    gemini_max_output_tokens: Annotated[int, Field(gt=0)] = 2048
    gemini_timeout_seconds: float = 45.0
    gemini_thinking_budget: Annotated[int, Field(ge=0)] = 0
    """Reasoning tokens allowed per call. Classification is a shallow task; 0 keeps
    latency and cost down. Raise if you see systematic misclassification."""
    llm_concurrency: Annotated[int, Field(gt=0, le=32)] = 5

    # ---- Pipeline behaviour ------------------------------------------------
    ocr_text_char_budget: Annotated[int, Field(gt=0)] = 12_000
    """Upper bound on OCR text sent to the LLM per document. Identifying markers for
    every supported document type appear near the start, so a head/tail slice is
    sufficient and keeps prompt cost bounded and predictable."""

    mask_sensitive_ids: bool = Field(
        default=True,
        description=(
            "Mask Aadhaar/bank numbers in generated filenames and the JSON report so "
            "the deliverable ZIP does not broadcast full government IDs."
        ),
    )
    min_classification_confidence: Annotated[float, Field(ge=0.0, le=1.0)] = 0.55
    """Below this the document is filed as UNKNOWN rather than guessed at, so a
    reviewer sees it instead of it being silently mis-filed."""

    review_confidence: Annotated[float, Field(ge=0.0, le=1.0)] = 0.75
    """Documents classified confidently enough to file, but not confidently enough
    to trust unsupervised, are listed in the report's `needs_review`. Two thresholds
    rather than one: rejecting everything merely uncertain to Unknown would throw
    away a usable classification, while filing it silently hides the uncertainty."""

    @field_validator("cors_origins", "api_tokens", mode="before")
    @classmethod
    def _split_csv(cls, value: object) -> object:
        """Parse list env vars as comma-separated, with JSON arrays still accepted.

        Comma-separated is the documented form (`HRDOC_API_TOKENS=a,b`) because it
        is what people actually type into a `.env`. Since `NoDecode` disabled the
        built-in JSON handling, the bracket form is re-accepted here so deployments
        already passing `["a","b"]` keep working.
        """
        if not isinstance(value, str):
            return value
        text = value.strip()
        if not text:
            return []
        if text.startswith("["):
            try:
                return json.loads(text)
            except json.JSONDecodeError as exc:
                raise ValueError(
                    f"value looks like a JSON array but does not parse: {exc}"
                ) from exc
        return [item.strip() for item in text.split(",") if item.strip()]

    @property
    def auth_enabled(self) -> bool:
        return bool(self.api_tokens)

    @property
    def docai_configured(self) -> bool:
        return bool(self.gcp_project_id and self.docai_processor_id)

    @property
    def gemini_configured(self) -> bool:
        return bool(self.gemini_api_key.get_secret_value())

    @property
    def docai_processor_path(self) -> str:
        base = (
            f"projects/{self.gcp_project_id}/locations/{self.docai_location}"
            f"/processors/{self.docai_processor_id}"
        )
        if self.docai_processor_version:
            return f"{base}/processorVersions/{self.docai_processor_version}"
        return base

    @property
    def docai_api_endpoint(self) -> str:
        return f"{self.docai_location}-documentai.googleapis.com"

    def validate_for_startup(self) -> list[str]:
        """Return human-readable configuration problems.

        Fatal in production, logged as warnings elsewhere so the service can still
        boot for local UI work against stub engines.
        """
        problems: list[str] = []
        if not self.docai_configured:
            problems.append(
                "Document AI is not configured (set HRDOC_GCP_PROJECT_ID and "
                "HRDOC_DOCAI_PROCESSOR_ID)."
            )
        if not self.gemini_configured:
            problems.append("Gemini is not configured (set HRDOC_GEMINI_API_KEY).")
        if not self.auth_enabled:
            problems.append("No API tokens configured; upload endpoints are unauthenticated.")
        if self.max_file_bytes > self.max_total_bytes:
            problems.append("max_file_bytes exceeds max_total_bytes.")
        return problems


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
