"""Reranker factory with lazy dependency loading.

sentence-transformers is NOT imported at module level — the service starts
without it when RERANK_ENABLED=false.
"""

import logging
import time
from enum import StrEnum

from config import settings

from .base import BaseReranker

logger = logging.getLogger(__name__)


class ComponentStatus(StrEnum):
    DISABLED = "disabled"
    MISSING_DEPENDENCY = "missing_dependency"
    DOWNLOADING = "downloading"
    LOADING = "loading"
    READY = "ready"
    FAILED = "failed"


_reranker: BaseReranker | None = None
_status: ComponentStatus = ComponentStatus.DISABLED
_last_error: str = ""
_started_at: float | None = None
_cached: bool | None = None


def get_reranker_status() -> dict:
    """Return current reranker component status."""
    elapsed = max(0.0, time.monotonic() - _started_at) if _started_at is not None else 0.0
    slow = (
        _status in (ComponentStatus.DOWNLOADING, ComponentStatus.LOADING)
        and elapsed >= settings.optional_model_notice_seconds
    )
    if _status == ComponentStatus.DISABLED:
        message = "Reranker 未启用；检索使用 RRF 排序"
    elif _status == ComponentStatus.DOWNLOADING:
        message = "Reranker 模型正在后台下载"
    elif _status == ComponentStatus.LOADING:
        message = "Reranker 模型正在后台加载和预热"
    elif _status == ComponentStatus.READY:
        message = "Reranker 模型已就绪"
    elif _status in (ComponentStatus.FAILED, ComponentStatus.MISSING_DEPENDENCY):
        message = "Reranker 不可用；检索已自动降级为 RRF 排序"
    else:
        message = "Reranker 正在初始化"
    if slow:
        message += "；已超过提示时间，但后台任务仍会继续，不会关闭服务"
    return {
        "status": _status.value,
        "last_error": _last_error,
        "model": settings.rerank_model if settings.rerank_enabled else None,
        "enabled": settings.rerank_enabled,
        "optional": True,
        "cached": _cached,
        "elapsed_seconds": round(elapsed, 1),
        "notice_seconds": settings.optional_model_notice_seconds,
        "continuing_in_background": _status in (ComponentStatus.DOWNLOADING, ComponentStatus.LOADING),
        "slow": slow,
        "message": message,
        "manual_command": "python -m tools.download_models --reranker",
    }


def create_reranker() -> BaseReranker | None:
    global _reranker, _status, _last_error, _started_at

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
        _started_at = time.monotonic()
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
    if _status not in (ComponentStatus.READY, ComponentStatus.FAILED, ComponentStatus.DOWNLOADING):
        _status = ComponentStatus.LOADING
    return False


def set_reranker_ready():
    global _status, _last_error
    _status = ComponentStatus.READY
    _last_error = ""


def set_reranker_phase(status: str, *, cached: bool) -> None:
    """Publish a non-terminal model lifecycle phase from the loader thread."""
    global _status, _cached, _started_at
    _status = ComponentStatus(status)
    _cached = cached
    if _started_at is None:
        _started_at = time.monotonic()


def set_reranker_failed(error: str):
    global _status, _last_error
    _status = ComponentStatus.FAILED
    _last_error = error
    logger.error("reranker failed: %s", error)
