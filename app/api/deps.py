"""Request-scoped dependencies."""

from __future__ import annotations

import secrets
from typing import Annotated

from fastapi import Depends, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.config import Settings
from app.errors import AuthenticationError, ServiceUnavailableError
from app.services.pipeline import DocumentPipeline

_bearer = HTTPBearer(auto_error=False, description="Static service token")


def get_settings_from_app(request: Request) -> Settings:
    """Resolve settings from the running app rather than the global cache.

    `Depends(get_settings)` would bind the module-level cached accessor, which
    means an app built with `create_app(custom_settings)` would silently ignore
    them. Reading from `app.state` keeps the app instance authoritative.
    """
    return request.app.state.settings  # type: ignore[no-any-return]


SettingsDep = Annotated[Settings, Depends(get_settings_from_app)]


def get_pipeline(request: Request) -> DocumentPipeline:
    pipeline: DocumentPipeline | None = getattr(request.app.state, "pipeline", None)
    if pipeline is None:  # pragma: no cover - only reachable if lifespan didn't run
        raise ServiceUnavailableError("The processing pipeline is not initialised.")
    return pipeline


PipelineDep = Annotated[DocumentPipeline, Depends(get_pipeline)]


async def require_api_token(
    settings: SettingsDep,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)] = None,
) -> None:
    """Validate the bearer token when any are configured.

    Comparison is constant-time so a token cannot be recovered by timing the
    endpoint.
    """
    if not settings.auth_enabled:
        return
    if credentials is None or credentials.scheme.lower() != "bearer":
        raise AuthenticationError("A bearer token is required.")

    presented = credentials.credentials
    if not any(
        secrets.compare_digest(presented, token.get_secret_value()) for token in settings.api_tokens
    ):
        raise AuthenticationError("Invalid API token.")


AuthDep = Annotated[None, Depends(require_api_token)]
