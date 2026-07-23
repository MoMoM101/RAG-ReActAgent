"""OCR factory with lazy dependency loading.

DocTR/paddle are NOT imported at module level — the service starts
without them when OCR_ENABLED=false.
"""

import logging
import time
from enum import StrEnum

from config import settings

from .base import BaseOCR

logger = logging.getLogger(__name__)


class ComponentStatus(StrEnum):
    DISABLED = "disabled"
    MISSING_DEPENDENCY = "missing_dependency"
    DOWNLOADING = "downloading"
    LOADING = "loading"
    READY = "ready"
    FAILED = "failed"


_ocr: BaseOCR | None = None
_status: ComponentStatus = ComponentStatus.DISABLED
_last_error: str = ""
_started_at: float | None = None
_cached: bool | None = None


def get_ocr_status() -> dict:
    """Return current OCR component status."""
    elapsed = max(0.0, time.monotonic() - _started_at) if _started_at is not None else 0.0
    slow = (
        _status in (ComponentStatus.DOWNLOADING, ComponentStatus.LOADING)
        and elapsed >= settings.optional_model_notice_seconds
    )
    if _status == ComponentStatus.DISABLED:
        message = "OCR 未启用；文本类文档和聊天功能不受影响"
    elif _status == ComponentStatus.DOWNLOADING:
        message = "OCR 模型正在后台下载"
    elif _status == ComponentStatus.LOADING:
        message = "OCR 模型正在后台加载"
    elif _status == ComponentStatus.READY:
        message = "OCR 模型已就绪"
    elif _status in (ComponentStatus.FAILED, ComponentStatus.MISSING_DEPENDENCY):
        message = "OCR 不可用；可手动安装模型或关闭 OCR，其他功能不受影响"
    else:
        message = "OCR 正在初始化"
    if slow:
        message += "；已超过提示时间，但后台任务仍会继续，不会关闭服务"
    return {
        "status": _status.value,
        "last_error": _last_error,
        "enabled": settings.ocr_enabled,
        "optional": True,
        "cached": _cached,
        "elapsed_seconds": round(elapsed, 1),
        "notice_seconds": settings.optional_model_notice_seconds,
        "continuing_in_background": _status in (ComponentStatus.DOWNLOADING, ComponentStatus.LOADING),
        "slow": slow,
        "message": message,
        "manual_command": "python -m tools.download_models --ocr",
    }


def create_ocr() -> BaseOCR | None:
    global _ocr, _status, _last_error, _started_at

    if not settings.ocr_enabled:
        _status = ComponentStatus.DISABLED
        return None

    if _ocr is not None:
        return _ocr

    try:
        from .paddle_ocr import DoctrOCREngine
    except ImportError as exc:
        _status = ComponentStatus.MISSING_DEPENDENCY
        _last_error = str(exc)
        logger.warning("OCR dependency unavailable: %s", exc)
        return None

    try:
        _status = ComponentStatus.LOADING
        _started_at = time.monotonic()
        _ocr = DoctrOCREngine()
    except Exception as exc:
        _status = ComponentStatus.FAILED
        _last_error = str(exc)
        logger.error("OCR init failed: %s", exc)
        return None

    return _ocr


def preload_ocr_async():
    """Preload OCR model in background."""
    if not settings.ocr_enabled:
        return
    r = create_ocr()
    if r is not None:
        r.preload_async()


def is_ocr_ready() -> bool:
    global _status
    r = create_ocr()
    if r is None:
        return False
    if hasattr(r, "ready") and r.ready:
        _status = ComponentStatus.READY
        return True
    if _status not in (
        ComponentStatus.READY,
        ComponentStatus.FAILED,
        ComponentStatus.MISSING_DEPENDENCY,
        ComponentStatus.DOWNLOADING,
    ):
        _status = ComponentStatus.LOADING
    return False


def set_ocr_ready():
    global _status, _last_error
    _status = ComponentStatus.READY
    _last_error = ""


def set_ocr_phase(status: str, *, cached: bool) -> None:
    """Publish a non-terminal model lifecycle phase from the loader thread."""
    global _status, _cached, _started_at
    _status = ComponentStatus(status)
    _cached = cached
    if _started_at is None:
        _started_at = time.monotonic()


def set_ocr_failed(error: str):
    global _status, _last_error
    _status = ComponentStatus.FAILED
    _last_error = error
    logger.error("OCR failed: %s", error)
