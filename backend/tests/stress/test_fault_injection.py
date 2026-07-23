"""Fault injection tests: verify degradation behavior under failure conditions."""

import pytest


@pytest.mark.filterwarnings("ignore:Failed to obtain server version.*:UserWarning")
class TestRetrievalFallbacks:
    async def test_keyword_only_fallback_when_qdrant_unavailable(self):
        from config import settings
        from rag.retriever import hybrid_search

        old_host = settings.qdrant_host
        try:
            settings.qdrant_host = "127.0.0.1:19999"
            results = await hybrid_search("测试")
            assert isinstance(results, list)
        finally:
            settings.qdrant_host = old_host

    async def test_semantic_only_fallback(self):
        from config import settings
        from rag.retriever import hybrid_search

        old_sem = settings.rrf_semantic_weight
        old_kw = settings.rrf_keyword_weight
        try:
            settings.rrf_keyword_weight = 0.0
            settings.rrf_semantic_weight = 2.0
            results = await hybrid_search("测试")
            assert isinstance(results, list)
        finally:
            settings.rrf_semantic_weight = old_sem
            settings.rrf_keyword_weight = old_kw

    async def test_both_paths_unavailable_raises_retrieval_error(self):
        from unittest.mock import patch

        import pytest as pt

        from config import settings
        from rag.retriever import RetrievalError, hybrid_search
        from textdb.bm25_search import BM25Search

        old_host = settings.qdrant_host
        try:
            settings.qdrant_host = "127.0.0.1:19999"
            with patch.object(BM25Search, "search", side_effect=RuntimeError("BM25 unavailable")), \
                 pt.raises(RetrievalError):
                await hybrid_search("测试")
        finally:
            settings.qdrant_host = old_host
