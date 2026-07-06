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
        docs = [
            "Python is a programming language",
            "The weather is nice today",
            "Python is used for data science",
        ]
        results = await reranker.rerank("What is Python?", docs)
        assert len(results) == 3
        scores = {i: s for i, s in results}
        assert scores[0] > scores[1] or scores[2] > scores[1]
