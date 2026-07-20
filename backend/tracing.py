"""Lightweight tracing with OpenTelemetry auto-detection.

Uses OpenTelemetry if installed, otherwise falls back to
structured logging with request-id propagation via contextvars.
"""

from __future__ import annotations

import logging
import time
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any

logger = logging.getLogger(__name__)

_request_id: ContextVar[str] = ContextVar("request_id", default="")

# Lazy OTel detection
_otel_tracer: Any = None
_otel_checked = False


def _get_otel_tracer():
    global _otel_tracer, _otel_checked
    if not _otel_checked:
        _otel_checked = True
        try:
            from opentelemetry import trace
            _otel_tracer = trace.get_tracer("rag-agent")
        except ImportError:
            pass
    return _otel_tracer


def set_request_id(rid: str) -> None:
    _request_id.set(rid)


def get_request_id() -> str:
    return _request_id.get()


def peek_request_id() -> str:
    rid = _request_id.get()
    return rid or "N/A"


@contextmanager
def span(name: str, **attrs: Any):
    """Create a tracing span. Uses OTel if available, otherwise logs.

    Usage:
        with span("agent.llm_call", model="gpt-4o"):
            ...
    """
    tracer = _get_otel_tracer()
    rid = peek_request_id()
    t0 = time.time()

    if tracer is not None:
        with tracer.start_as_current_span(name, attributes=attrs) as otel_span:
            otel_span.set_attribute("request_id", rid)
            try:
                yield otel_span
            except Exception:
                otel_span.set_attribute("error", True)
                raise
    else:
        logger.debug("span_start name=%s rid=%s attrs=%s", name, rid, attrs)
        try:
            yield None
        except Exception:
            elapsed = (time.time() - t0) * 1000
            logger.debug("span_error name=%s rid=%s elapsed_ms=%.0f", name, rid, elapsed)
            raise
        else:
            elapsed = (time.time() - t0) * 1000
            logger.debug("span_end name=%s rid=%s elapsed_ms=%.0f", name, rid, elapsed)
