import pytest

from reranker.base import BaseReranker


class DummyReranker(BaseReranker):
    """Always returns documents in the same order with fake scores."""
    async def rerank(self, query: str, documents: list[str]) -> list[tuple[int, float]]:
        return [(i, 1.0 - i * 0.1) for i in range(len(documents))]


class TestBaseReranker:
    @pytest.mark.asyncio
    async def test_abstract_interface(self):
        reranker = DummyReranker()
        results = await reranker.rerank("test query", ["doc a", "doc b", "doc c"])
        assert len(results) == 3
        assert results[0] == (0, 1.0)
        assert results[1] == (1, 0.9)
        assert results[2] == (2, 0.8)

    @pytest.mark.asyncio
    async def test_empty_documents(self):
        reranker = DummyReranker()
        results = await reranker.rerank("query", [])
        assert results == []


class TestCrossEncoderReranker:
    @pytest.mark.slow
    @pytest.mark.asyncio
    async def test_real_reranker(self):
        try:
            from reranker.cross_encoder import CrossEncoderReranker
        except (ImportError, OSError) as e:
            pytest.skip(f"Reranker model not available: {e}")
            return

        reranker = CrossEncoderReranker("BAAI/bge-reranker-v2-m3")
        reranker.preload_async()
        # Wait up to 120s for model to load (download on first run)
        import asyncio
        deadline = asyncio.get_event_loop().time() + 120
        while not reranker.ready:
            if asyncio.get_event_loop().time() > deadline:
                pytest.skip("Reranker model load timed out")
            await asyncio.sleep(1)

        docs = [
            "Python is a programming language",
            "The weather is nice today",
            "Python is used for data science",
        ]
        results = await reranker.rerank("What is Python?", docs)
        assert len(results) == 3
        scores = {i: s for i, s in results}
        assert scores[0] > scores[1] or scores[2] > scores[1]

    @pytest.mark.asyncio
    async def test_reranker_unready_returns_neutral_scores(self):
        """When model is not ready, rerank() returns neutral 0.5 scores for all docs."""
        from reranker.cross_encoder import CrossEncoderReranker
        reranker = CrossEncoderReranker("BAAI/bge-reranker-v2-m3")
        # Do NOT call preload_async — simulate unready state
        docs = ["doc a", "doc b", "doc c"]
        results = await reranker.rerank("query", docs)
        assert len(results) == 3
        assert all(score == 0.5 for _, score in results)

    @pytest.mark.asyncio
    async def test_reranker_empty_docs(self):
        """Empty document list returns empty result."""
        from reranker.cross_encoder import CrossEncoderReranker
        reranker = CrossEncoderReranker("BAAI/bge-reranker-v2-m3")
        results = await reranker.rerank("query", [])
        assert results == []


class TestRerankerWarmup:
    def test_ready_only_after_warmup_inference(self, monkeypatch):
        """加载完成后必须先跑一次 dummy 推理(预热),再置 ready。"""
        import sys
        import threading
        import time as _time
        import types

        release = threading.Event()
        calls: list = []

        class FakeCrossEncoder:
            def __init__(self, model_name):
                pass

            def predict(self, pairs):
                calls.append(pairs)
                release.wait(timeout=5)
                return [0.5] * len(pairs)

        fake_module = types.ModuleType("sentence_transformers")
        fake_module.CrossEncoder = FakeCrossEncoder
        monkeypatch.setitem(sys.modules, "sentence_transformers", fake_module)

        from reranker.cross_encoder import CrossEncoderReranker

        r = CrossEncoderReranker("fake-model-for-warmup-test")
        r.preload_async()

        # 等到预热 predict 被调用(模型"加载"是瞬时的)
        deadline = _time.time() + 2
        while not calls and _time.time() < deadline:
            _time.sleep(0.01)
        assert calls, "warmup predict was never called"
        assert calls[0] == [["warmup", "warmup"]]

        # 预热尚未完成(predict 阻塞中)→ 不得 ready
        assert r.ready is False

        # 放行预热 → ready
        release.set()
        deadline = _time.time() + 2
        while not r.ready and _time.time() < deadline:
            _time.sleep(0.01)
        assert r.ready is True
