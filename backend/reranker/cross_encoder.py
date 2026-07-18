"""Cross-encoder reranker with lazy model loading.

sentence_transformers is imported inside the background loading thread,
not at module level — the service can start without the dependency.
"""

import asyncio
import logging
import os
import threading
import time
from pathlib import Path

from .base import BaseReranker

logger = logging.getLogger(__name__)


def _is_model_cached(model_name: str) -> bool:
    """Check if model files are fully downloaded in HF cache."""
    parts = model_name.split("/")
    if len(parts) >= 2:
        org, name = parts[0], parts[1]
    else:
        org, name = "sentence-transformers", model_name
    cache_dir = Path.home() / ".cache" / "huggingface" / "hub"
    model_dir = cache_dir / f"models--{org}--{name}"
    if not model_dir.is_dir():
        return False
    return any(
        any(snap.glob("*.safetensors")) or any(snap.glob("*.bin"))
        for snap in model_dir.glob("snapshots/*")
    )


class CrossEncoderReranker(BaseReranker):
    def __init__(self, model_name: str):
        self._model = None
        self._ready = False
        self._model_name = model_name

    def preload_async(self):
        """Load model in background thread — never block startup."""
        cached = _is_model_cached(self._model_name)

        def _load():
            try:
                from sentence_transformers import CrossEncoder
            except ImportError as exc:
                logger.warning("sentence-transformers not installed, reranker unavailable")
                self._ready = False
                from .factory import set_reranker_failed
                set_reranker_failed(str(exc))
                return

            try:
                os.environ.setdefault("HF_HUB_DISABLE_PROGRESS_BARS", "1")
                if not cached:
                    logger.info("reranker downloading model %s ...", self._model_name)
                start = time.time()
                self._model = CrossEncoder(self._model_name)
                # Warmup: torch 首次推理有数秒惰性初始化,在后台线程消化掉,
                # 避免算进第一个真实请求导致检索超时
                self._model.predict([["warmup", "warmup"]])
                elapsed = time.time() - start
                self._ready = True
                logger.info("reranker ready model=%s elapsed=%.0fs", self._model_name, elapsed)
                from .factory import set_reranker_ready
                set_reranker_ready()
            except Exception as e:
                logger.error("reranker load failed: %s", e)
                self._ready = False
                from .factory import set_reranker_failed
                set_reranker_failed(str(e))

        threading.Thread(target=_load, daemon=True).start()

    @property
    def ready(self) -> bool:
        return self._ready

    async def rerank(self, query: str, documents: list[str]) -> list[tuple[int, float]]:
        if not documents:
            return []
        if not self._ready or self._model is None:
            return [(i, 0.5) for i in range(len(documents))]
        pairs = [[query, doc] for doc in documents]
        scores = await asyncio.to_thread(self._model.predict, pairs)
        indexed = [(i, float(scores[i])) for i in range(len(documents))]
        indexed.sort(key=lambda x: x[1], reverse=True)
        return indexed
