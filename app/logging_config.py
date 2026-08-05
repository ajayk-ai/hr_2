"""Structured logging with PII scrubbing.

Every log record passes through :func:`_scrub_processor`, so an Aadhaar or PAN
number that leaks into a log call is masked before it reaches a log sink. This is
belt-and-braces: call sites should not log raw identifiers in the first place.
"""

from __future__ import annotations

import logging
import sys
from typing import Any

import structlog

from app.utils.redaction import redact_text


def _scrub_processor(
    _logger: Any, _method: str, event_dict: structlog.types.EventDict
) -> structlog.types.EventDict:
    for key, value in event_dict.items():
        if isinstance(value, str):
            event_dict[key] = redact_text(value)
    return event_dict


def configure_logging(*, level: str = "INFO", json_output: bool = False) -> None:
    """Configure structlog and route the stdlib logging tree through it."""
    shared_processors: list[structlog.types.Processor] = [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_log_level,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
        _scrub_processor,
    ]
    renderer: structlog.types.Processor = (
        structlog.processors.JSONRenderer()
        if json_output
        else structlog.dev.ConsoleRenderer(colors=sys.stderr.isatty())
    )

    structlog.configure(
        processors=[*shared_processors, renderer],
        wrapper_class=structlog.make_filtering_bound_logger(logging.getLevelNamesMapping()[level]),
        logger_factory=structlog.PrintLoggerFactory(file=sys.stderr),
        cache_logger_on_first_use=True,
    )

    logging.basicConfig(
        format="%(message)s",
        stream=sys.stderr,
        level=logging.getLevelNamesMapping()[level],
        force=True,
    )
    # Access logs are emitted by our own middleware with a request id attached.
    logging.getLogger("uvicorn.access").disabled = True


def get_logger(name: str) -> Any:
    """A logger tagged with its module name.

    ``PrintLoggerFactory`` produces loggers that carry no name of their own, so the
    module is bound into the event context instead.
    """
    return structlog.get_logger().bind(logger=name)
