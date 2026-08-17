"""Application factory, lifespan wiring and cross-cutting middleware."""

from __future__ import annotations

import time
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

import structlog
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.formparsers import MultiPartParser

from app import __version__
from app.api.routes import router
from app.config import Settings, get_settings
from app.errors import AppError
from app.logging_config import configure_logging, get_logger
from app.services.classifier import build_classifier
from app.services.ocr import build_ocr_engine
from app.services.pipeline import DocumentPipeline
from app.tls import configure_tls

logger = get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings: Settings = app.state.settings
    configure_logging(level=settings.log_level, json_output=settings.log_json)

    # Before any client exists: an SSL context keeps the verification settings it
    # was built with, so injecting afterwards would leave the first-created
    # clients still validating against certifi alone.
    system_trust_store = configure_tls(use_system_trust_store=settings.use_system_trust_store)

    problems = settings.validate_for_startup()
    if problems and settings.environment == "production":
        raise RuntimeError("Invalid production configuration: " + " ".join(problems))
    for problem in problems:
        logger.warning("config.problem", problem=problem)

    # Starlette spools multipart parts above 1 MB to a temp file on disk. This
    # service promises never to persist uploads, so both thresholds are raised to
    # the batch cap: parts stay in memory, and an oversized file is rejected by our
    # own bounded reader (a clean 413) instead of by the parser (an opaque 400).
    # Peak memory per in-flight request is therefore bounded by max_total_bytes.
    MultiPartParser.spool_max_size = settings.max_total_bytes
    MultiPartParser.max_part_size = settings.max_total_bytes

    ocr_engine = build_ocr_engine(settings)
    classifier = build_classifier(settings)

    app.state.ocr_engine = ocr_engine
    app.state.classifier = classifier
    app.state.pipeline = DocumentPipeline(
        settings=settings, ocr_engine=ocr_engine, classifier=classifier
    )

    logger.info(
        "app.started",
        version=__version__,
        environment=settings.environment,
        ocr_engine=ocr_engine.name,
        classifier=classifier.name,
        system_trust_store=system_trust_store,
    )
    try:
        yield
    finally:
        await ocr_engine.aclose()
        logger.info("app.stopped")


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()

    app = FastAPI(
        title="HR Document Classifier",
        version=__version__,
        description=(
            "Classifies, renames and packages a candidate's onboarding documents. "
            "Uploads are processed in memory and never written to disk."
        ),
        lifespan=lifespan,
        docs_url="/docs" if settings.environment != "production" else None,
        redoc_url=None,
    )
    # Authoritative for the whole app: dependencies and lifespan both read it here,
    # so `create_app(custom_settings)` is honoured everywhere.
    app.state.settings = settings

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=False,
        allow_methods=["GET", "POST", "OPTIONS"],
        allow_headers=["Authorization", "Content-Type"],
        # Without this the browser hides our custom headers from fetch().
        expose_headers=[
            "Content-Disposition",
            "X-Request-Id",
            "X-Files-Failed",
            "X-Needs-Review",
        ],
    )

    @app.middleware("http")
    async def request_context(request: Request, call_next):  # type: ignore[no-untyped-def]
        request_id = request.headers.get("X-Request-Id") or uuid.uuid4().hex
        # Handlers reuse this id (see the process endpoint) so the HTTP response,
        # the logs and the id inside report.json all agree.
        request.state.request_id = request_id
        structlog.contextvars.bind_contextvars(
            request_id=request_id, path=request.url.path, method=request.method
        )
        started = time.perf_counter()
        try:
            response = await call_next(request)
        except Exception:
            logger.exception(
                "http.unhandled_error",
                duration_ms=int((time.perf_counter() - started) * 1000),
            )
            raise
        finally:
            structlog.contextvars.unbind_contextvars("request_id", "path", "method")

        duration_ms = int((time.perf_counter() - started) * 1000)
        logger.info("http.request", status=response.status_code, duration_ms=duration_ms)
        response.headers["X-Request-Id"] = request_id
        return response

    @app.exception_handler(AppError)
    async def app_error_handler(_: Request, exc: AppError) -> JSONResponse:
        # `detail` can quote upstream payloads, so it is logged but not returned.
        logger.warning("app.error", code=exc.code, message=exc.message, detail=exc.detail)
        return JSONResponse(
            status_code=exc.status_code,
            content={"error": {"code": exc.code, "message": exc.message}},
        )

    @app.exception_handler(RequestValidationError)
    async def validation_handler(_: Request, exc: RequestValidationError) -> JSONResponse:
        return JSONResponse(
            status_code=422,
            content={
                "error": {
                    "code": "validation_error",
                    "message": "The request could not be validated.",
                    "fields": exc.errors(),
                }
            },
        )

    @app.exception_handler(Exception)
    async def unhandled_handler(_: Request, exc: Exception) -> JSONResponse:
        logger.exception("app.unhandled_error", error=type(exc).__name__)
        return JSONResponse(
            status_code=500,
            content={
                "error": {
                    "code": "internal_error",
                    "message": "An unexpected error occurred.",
                }
            },
        )

    app.include_router(router)

    # Serve the built frontend from the same process, so one uvicorn command runs
    # the whole service. The `assets` mount is registered first so its files win
    # over the catch-all; the catch-all then serves index.html for the single
    # page app (html=True resolves "/" to index.html).
    frontend_dist = Path(__file__).resolve().parents[1] / "frontend" / "dist"
    if frontend_dist.is_dir():
        app.mount(
            "/assets",
            StaticFiles(directory=frontend_dist / "assets"),
            name="frontend-assets",
        )
        app.mount("/", StaticFiles(directory=frontend_dist, html=True), name="frontend")
        logger.info(
            "app.frontend_served", dist=str(frontend_dist), url="/"
        )
    else:
        logger.warning(
            "app.frontend_missing",
            detail=f"Frontend build not found at {frontend_dist}; "
            "run `cd frontend && npm run build` to serve the UI from this process.",
        )

    return app


app = create_app()

if __name__ == "__main__":
    # `uv run python -m app.main` reads host/port from settings (HRDOC_HOST /
    # HRDOC_PORT), so they live in .env instead of being hardcoded into every
    # launch script. `uv run uvicorn app.main:app --host ... --port ...` still
    # works too, for anyone who wants to override them ad hoc.
    #
    # Passed as the `app` object (not the "app.main:app" import string) so the
    # module isn't re-imported a second time under a different name -- with the
    # string form, --reload's requirement to re-import by path would otherwise
    # run this file's top-level code (and lifespan startup) twice.
    import uvicorn

    settings = get_settings()
    uvicorn.run(app, host=settings.host, port=settings.port)
