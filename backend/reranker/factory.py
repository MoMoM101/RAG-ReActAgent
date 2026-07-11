"""Reranker factory with lazy dependency loading.

sentence-transformers is NOT imported at module level — the service starts
without it when RERANK_ENABLED=false.
"""

import logging
from enum import StrEnum

from config import settings

from .base import BaseReranker

logger = logging.getLogger(__name__)


class ComponentStatus(StrEnum):
    DISABLED = "disabled"
    MISSING_DEPENDENCY = "missing_dependency"
    LOADING = "loading"
    READY = "ready"
    FAILED = "failed"


_reranker: BaseReranker | None = None
_status: ComponentStatus = ComponentStatus.DISABLED
_last_error: str = ""


def get_reranker_status() -> dict:
    """Return current reranker component status."""
    return {
        "status": _status.value,
        "last_error": _last_error,
        "model": settings.rerank_model if settings.rerank_enabled else None,
    }


def create_reranker() -> BaseReranker | None:
    global _reranker, _status, _last_error

    if not settings.rerank_enabled:
        _status = ComponentStatus.DISABLED
        return None

    if _reranker is not None:
        return _reranker

    try:
        from .cross_encoder import CrossEncoderReranker  # noqa: F811
    except ImportError as exc:
        _status = ComponentStatus.MISSING_DEPENDENCY
        _last_error = str(exc)
        logger.warning("reranker dependency unavailable: %s", exc)
        return None

    try:
        _status = ComponentStatus.LOADING
        _reranker = CrossEncoderReranker(settings.rerank_model)
    except Exception as exc:
        _status = ComponentStatus.FAILED
        _last_error = str(exc)
        logger.error("reranker init failed: %s", exc)
        return None

    return _reranker


def preload_reranker_async():
    """Check cache and start background download if needed."""
    if not settings.rerank_enabled:
        return
    r = create_reranker()
    if r is not None:
        r.preload_async()


def is_reranker_ready() -> bool:
    global _status
    r = create_reranker()
    if r is None:
        return False
    if hasattr(r, "ready") and r.ready:
        _status = ComponentStatus.READY
        return True
    if _status not in (ComponentStatus.READY, ComponentStatus.FAILED):
        _status = ComponentStatus.LOADING
    return False


def set_reranker_ready():
    global _status
    _status = ComponentStatus.READY


def set_reranker_failed(error: str):
    global _status, _last_error
    _status = ComponentStatus.FAILED
    _last_error = error
    logger.error("reranker failed: %s", error)
