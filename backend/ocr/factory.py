"""OCR factory with lazy dependency loading.

DocTR/paddle are NOT imported at module level — the service starts
without them when OCR_ENABLED=false.
"""

import logging
from enum import StrEnum

from config import settings

from .base import BaseOCR

logger = logging.getLogger(__name__)


class ComponentStatus(StrEnum):
    DISABLED = "disabled"
    MISSING_DEPENDENCY = "missing_dependency"
    LOADING = "loading"
    READY = "ready"
    FAILED = "failed"


_ocr: BaseOCR | None = None
_status: ComponentStatus = ComponentStatus.DISABLED
_last_error: str = ""


def get_ocr_status() -> dict:
    """Return current OCR component status."""
    return {
        "status": _status.value,
        "last_error": _last_error,
        "enabled": settings.ocr_enabled,
    }


def create_ocr() -> BaseOCR | None:
    global _ocr, _status, _last_error

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
    if _status not in (ComponentStatus.READY, ComponentStatus.FAILED, ComponentStatus.MISSING_DEPENDENCY):
        _status = ComponentStatus.LOADING
    return False


def set_ocr_ready():
    global _status
    _status = ComponentStatus.READY


def set_ocr_failed(error: str):
    global _status, _last_error
    _status = ComponentStatus.FAILED
    _last_error = error
    logger.error("OCR failed: %s", error)
