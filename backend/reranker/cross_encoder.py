import asyncio
import os
import threading
import time
from pathlib import Path

from sentence_transformers import CrossEncoder

from .base import BaseReranker


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
    return any(any(snap.glob("*.safetensors")) or any(snap.glob("*.bin")) for snap in model_dir.glob("snapshots/*"))


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
                os.environ.setdefault("HF_HUB_DISABLE_PROGRESS_BARS", "1")
                if not cached:
                    print("[Reranker] 正在下载模型，首次需 1-2 分钟...", flush=True)
                start = time.time()
                self._model = CrossEncoder(self._model_name)
                elapsed = time.time() - start
                self._ready = True
                print(f"[Reranker] 模型就绪，耗时 {elapsed:.0f}s", flush=True)
            except Exception as e:
                print(f"[Reranker] 加载失败: {e}", flush=True)
                if not cached:
                    print("[Reranker] 请在 .env 设置 hf_endpoint=https://hf-mirror.com", flush=True)
                self._ready = False

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
