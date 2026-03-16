"""Structured JSON logging with secret redaction and trace correlation."""

from __future__ import annotations

import logging
import re
from collections.abc import MutableMapping
from contextvars import ContextVar, Token
from typing import Any

import structlog
from opentelemetry import trace

from app.config import Settings

_CORRELATION_ID: ContextVar[str | None] = ContextVar("correlation_id", default=None)

_REDACTED = "[REDACTED]"
_REDACT_KEY_PATTERN = re.compile(
    r"(authorization|x[-_]?hub[-_]?signature[-_]?256|secret|api[-_]?key|fernet[-_]?key|password|token)",
    re.IGNORECASE,
)


def get_correlation_id() -> str | None:
    return _CORRELATION_ID.get()


def bind_correlation_id(correlation_id: str) -> Token[str | None]:
    return _CORRELATION_ID.set(correlation_id)


def clear_correlation_id(token: Token[str | None] | None = None) -> None:
    if token is not None:
        _CORRELATION_ID.reset(token)
    else:
        _CORRELATION_ID.set(None)


def _should_redact_key(key: str) -> bool:
    return bool(_REDACT_KEY_PATTERN.search(key))


def _redact_value(key: str, value: object) -> object:
    if _should_redact_key(key) and value is not None:
        return _REDACTED
    return value


def _redact_mapping(data: dict[str, Any]) -> dict[str, Any]:
    return {key: _redact_value(key, value) for key, value in data.items()}


def _add_service_context(
    _logger: logging.Logger,
    _method_name: str,
    event_dict: MutableMapping[str, Any],
) -> MutableMapping[str, Any]:
    correlation_id = _CORRELATION_ID.get()
    if correlation_id is not None:
        event_dict.setdefault("correlation_id", correlation_id)

    span = trace.get_current_span()
    span_context = span.get_span_context()
    if span_context.is_valid:
        event_dict["trace_id"] = format(span_context.trace_id, "032x")
        event_dict["span_id"] = format(span_context.span_id, "016x")

    return event_dict


def _redact_processor(
    _logger: logging.Logger,
    _method_name: str,
    event_dict: MutableMapping[str, Any],
) -> MutableMapping[str, Any]:
    redacted = _redact_mapping(dict(event_dict))
    event_dict.clear()
    event_dict.update(redacted)
    return event_dict


def configure_logging(settings: Settings) -> None:
    level = getattr(logging, settings.log_level.upper(), logging.INFO)
    root = logging.getLogger()
    root.setLevel(level)
    if not root.handlers:
        handler = logging.StreamHandler()
        handler.setFormatter(logging.Formatter("%(message)s"))
        root.addHandler(handler)

    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.stdlib.add_log_level,
            structlog.stdlib.add_logger_name,
            structlog.processors.TimeStamper(fmt="iso", key="timestamp"),
            _add_service_context,
            _redact_processor,
            structlog.processors.EventRenamer("message"),
            structlog.processors.JSONRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(level),
        logger_factory=structlog.stdlib.LoggerFactory(),
        cache_logger_on_first_use=True,
    )

    structlog.contextvars.bind_contextvars(service=settings.otel_service_name)


def reset_logging_for_tests() -> None:
    structlog.reset_defaults()
    root = logging.getLogger()
    root.handlers.clear()
    root.setLevel(logging.WARNING)
    clear_correlation_id()
