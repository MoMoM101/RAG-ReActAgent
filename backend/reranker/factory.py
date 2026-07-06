from config import settings

from .base import BaseReranker
from .cross_encoder import CrossEncoderReranker

_reranker: BaseReranker | None = None


def create_reranker() -> BaseReranker | None:
    global _reranker
    if not settings.rerank_enabled:
        return None
    if _reranker is None:
        _reranker = CrossEncoderReranker(settings.rerank_model)
    return _reranker


def preload_reranker_async():
    """Check cache and start background download if needed."""
    if not settings.rerank_enabled:
        return
    r = create_reranker()
    if r is not None and isinstance(r, CrossEncoderReranker):
        r.preload_async()


def is_reranker_ready() -> bool:
    r = create_reranker()
    if r is None:
        return False
    return isinstance(r, CrossEncoderReranker) and r.ready
