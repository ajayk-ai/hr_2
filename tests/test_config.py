"""Settings loading, exercised through the env and dotenv sources.

The rest of the suite builds `Settings(...)` directly with Python objects, which
bypasses pydantic-settings' source layer entirely. These tests deliberately go
through the environment and a real `.env` file, because that layer is where
list-typed fields get decoded and where a working config can still fail at boot.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from app.config import Settings


def _write_env(tmp_path: Path, body: str) -> Path:
    path = tmp_path / ".env"
    path.write_text(body, encoding="utf-8")
    return path


def test_dotenv_accepts_comma_separated_lists(tmp_path: Path) -> None:
    """The form documented in `.env.example`, and the one that used to crash.

    pydantic-settings JSON-decodes complex fields inside the env source, so a bare
    `http://localhost:5173` raised JSONDecodeError before any validator ran.
    """
    env_file = _write_env(
        tmp_path,
        "HRDOC_CORS_ORIGINS=http://localhost:5173,https://hr.example.com\n"
        "HRDOC_API_TOKENS=alpha,beta\n",
    )

    settings = Settings(_env_file=env_file)  # type: ignore[call-arg]

    assert settings.cors_origins == ["http://localhost:5173", "https://hr.example.com"]
    assert [token.get_secret_value() for token in settings.api_tokens] == ["alpha", "beta"]
    assert settings.auth_enabled is True


def test_dotenv_treats_empty_list_values_as_empty(tmp_path: Path) -> None:
    """`HRDOC_API_TOKENS=` is how `.env.example` ships; it must mean "no tokens"."""
    env_file = _write_env(tmp_path, "HRDOC_API_TOKENS=\nHRDOC_CORS_ORIGINS=\n")

    settings = Settings(_env_file=env_file)  # type: ignore[call-arg]

    assert settings.api_tokens == []
    assert settings.cors_origins == []
    assert settings.auth_enabled is False


def test_json_array_form_still_accepted(tmp_path: Path) -> None:
    """`NoDecode` disabled the built-in JSON path, so the validator re-adds it."""
    env_file = _write_env(tmp_path, 'HRDOC_CORS_ORIGINS=["https://a.test","https://b.test"]\n')

    settings = Settings(_env_file=env_file)  # type: ignore[call-arg]

    assert settings.cors_origins == ["https://a.test", "https://b.test"]


def test_malformed_json_array_reports_clearly(tmp_path: Path) -> None:
    """A truncated bracket form should name the problem, not fall back silently."""
    env_file = _write_env(tmp_path, 'HRDOC_CORS_ORIGINS=["https://a.test"\n')

    with pytest.raises(ValidationError, match="does not parse"):
        Settings(_env_file=env_file)  # type: ignore[call-arg]


def test_whitespace_around_entries_is_stripped(tmp_path: Path) -> None:
    env_file = _write_env(tmp_path, "HRDOC_CORS_ORIGINS= http://a.test , http://b.test \n")

    settings = Settings(_env_file=env_file)  # type: ignore[call-arg]

    assert settings.cors_origins == ["http://a.test", "http://b.test"]


def test_environment_variables_override_dotenv(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    env_file = _write_env(tmp_path, "HRDOC_CORS_ORIGINS=http://from-file.test\n")
    monkeypatch.setenv("HRDOC_CORS_ORIGINS", "http://from-env.test")

    settings = Settings(_env_file=env_file)  # type: ignore[call-arg]

    assert settings.cors_origins == ["http://from-env.test"]


def test_realistic_dotenv_loads_and_reports_no_problems(tmp_path: Path) -> None:
    """A fully populated `.env` should boot clean, mirroring the shipped example."""
    env_file = _write_env(
        tmp_path,
        "HRDOC_ENVIRONMENT=production\n"
        "HRDOC_CORS_ORIGINS=https://hr.example.com\n"
        "HRDOC_API_TOKENS=t0ken\n"
        "HRDOC_GCP_PROJECT_ID=hr-project-504507\n"
        "HRDOC_DOCAI_LOCATION=asia-south1\n"
        "HRDOC_DOCAI_PROCESSOR_ID=7682e85404771a77\n"
        "HRDOC_GEMINI_API_KEY=AIzaTestKey\n",
    )

    settings = Settings(_env_file=env_file)  # type: ignore[call-arg]

    assert settings.docai_configured is True
    assert settings.gemini_configured is True
    assert settings.validate_for_startup() == []
    # Regional processors need a region-specific endpoint, not the global one.
    assert settings.docai_api_endpoint == "asia-south1-documentai.googleapis.com"
    assert settings.docai_processor_path == (
        "projects/hr-project-504507/locations/asia-south1/processors/7682e85404771a77"
    )
