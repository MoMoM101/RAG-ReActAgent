from config import settings

from .base import BaseOCR
from .paddle_ocr import DoctrOCREngine

_ocr: BaseOCR | None = None


def create_ocr() -> BaseOCR | None:
    global _ocr
    if not settings.ocr_enabled:
        return None
    if _ocr is None:
        _ocr = DoctrOCREngine()
    return _ocr


def preload_ocr_async():
    if not settings.ocr_enabled:
        return
    r = create_ocr()
    if r is not None and isinstance(r, DoctrOCREngine):
        r.preload_async()


def is_ocr_ready() -> bool:
    r = create_ocr()
    if r is None:
        return False
    return isinstance(r, DoctrOCREngine) and r.ready
