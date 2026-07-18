import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

import rag.retriever as retriever
from rag.retriever import RetrievalError, _rrf_fusion, hybrid_search
from textdb.base import TextSearchResult
from vectordb.base import VectorSearchResult


def test_rrf_fusion_basic():
    vec = [VectorSearchResult(chunk_id="a", document_id="d1", text="t1", score=0.9)]
    txt = [TextSearchResult(chunk_id="b", document_id="d2", text="t2", score=5.0)]

    results = _rrf_fusion(vec, txt, k=2)
    assert len(results) == 2


def test_rrf_fusion_overlap():
    vec = [VectorSearchResult(chunk_id="a", document_id="d1", text="t1", score=0.9)]
    txt = [TextSearchResult(chunk_id="a", document_id="d1", text="t1", score=5.0)]

    results = _rrf_fusion(vec, txt, k=1)
    assert len(results) == 1
    assert results[0].source == "hybrid"


def test_rrf_fusion_empty():
    results = _rrf_fusion([], [], k=5)
    assert results == []


def test_rrf_fusion_sorting():
    vec = [
        VectorSearchResult(chunk_id="a", document_id="d1", text="best match", score=0.9),
        VectorSearchResult(chunk_id="b", document_id="d1", text="ok match", score=0.5),
    ]
    txt = [TextSearchResult(chunk_id="a", document_id="d1", text="best match", score=5.0)]

    results = _rrf_fusion(vec, txt, k=2)
    assert len(results) == 2
    assert results[0].chunk_id == "a"
    assert results[0].source == "hybrid"
    assert results[1].chunk_id == "b"
    assert results[1].source == "semantic"


def test_rrf_fusion_vector_only():
    vec = [
        VectorSearchResult(chunk_id="a", document_id="d1", text="t1", score=0.9),
        VectorSearchResult(chunk_id="b", document_id="d1", text="t2", score=0.7),
    ]
    results = _rrf_fusion(vec, [], k=2)
    assert len(results) == 2
    assert all(r.source == "semantic" for r in results)


def test_rrf_fusion_keyword_only():
    txt = [
        TextSearchResult(chunk_id="a", document_id="d1", text="t1", score=5.0),
        TextSearchResult(chunk_id="b", document_id="d1", text="t2", score=3.0),
    ]
    results = _rrf_fusion([], txt, k=2)
    assert len(results) == 2
    assert all(r.source == "keyword" for r in results)


def test_rrf_fusion_k_exceeds_results():
    vec = [VectorSearchResult(chunk_id="a", document_id="d1", text="t1", score=0.9)]
    txt = [TextSearchResult(chunk_id="b", document_id="d2", text="t2", score=5.0)]

    results = _rrf_fusion(vec, txt, k=10)
    assert len(results) == 2


def test_rrf_fusion_k_limit():
    vec = [VectorSearchResult(chunk_id=f"c{i}", document_id=f"d{i}", text=f"text{i}", score=0.9) for i in range(10)]
    txt = [TextSearchResult(chunk_id=f"c{i+5}", document_id=f"d{i+5}", text=f"text{i+5}", score=5.0) for i in range(10)]

    results = _rrf_fusion(vec, txt, k=5)
    assert len(results) == 5


def test_rrf_zero_weight_disables_source():
    vec = [VectorSearchResult("v1", "d1", "semantic", 0.9)]
    txt = [TextSearchResult("t1", "d2", "keyword", 4.2)]

    semantic_only = _rrf_fusion(vec, txt, k=5, semantic_weight=1.0, keyword_weight=0.0)
    keyword_only = _rrf_fusion(vec, txt, k=5, semantic_weight=0.0, keyword_weight=1.0)

    assert [r.chunk_id for r in semantic_only] == ["v1"]
    assert [r.chunk_id for r in keyword_only] == ["t1"]


def _disable_optional_retrieval_features(monkeypatch):
    monkeypatch.setattr(retriever.settings, "query_rewrite_enabled", False)
    monkeypatch.setattr(retriever.settings, "rrf_adaptive_enabled", False)
    monkeypatch.setattr(retriever.settings, "rrf_quality_prefilter_enabled", False)
    monkeypatch.setattr(retriever.settings, "dedup_enabled", False)


@pytest.mark.asyncio
async def test_hybrid_search_keeps_keyword_results_when_semantic_fails(monkeypatch):
    _disable_optional_retrieval_features(monkeypatch)
    embedding = MagicMock()
    embedding.embed_query = AsyncMock(side_effect=RuntimeError("embedding down"))
    keyword = TextSearchResult(
        chunk_id="kw", document_id="doc", text="keyword result", score=3.0
    )
    fts = MagicMock()
    fts.search = AsyncMock(return_value=[keyword])

    monkeypatch.setattr(retriever, "create_embedding", lambda: embedding)
    monkeypatch.setattr(retriever, "create_vectordb", AsyncMock(return_value=MagicMock()))
    monkeypatch.setattr(retriever, "BM25Search", lambda: fts)

    results = await hybrid_search("query", top_k=3)

    assert [r.chunk_id for r in results] == ["kw"]
    assert results[0].fallback_reason == "keyword_only_fallback"


@pytest.mark.asyncio
async def test_hybrid_search_keeps_semantic_results_when_keyword_fails(monkeypatch):
    _disable_optional_retrieval_features(monkeypatch)
    embedding = MagicMock()
    embedding.embed_query = AsyncMock(return_value=[0.1, 0.2])
    vector = VectorSearchResult(
        chunk_id="vec", document_id="doc", text="semantic result", score=0.9
    )
    vectordb = MagicMock()
    vectordb.search = AsyncMock(return_value=[vector])
    fts = MagicMock()
    fts.search = AsyncMock(side_effect=RuntimeError("bm25 down"))

    monkeypatch.setattr(retriever, "create_embedding", lambda: embedding)
    monkeypatch.setattr(retriever, "create_vectordb", AsyncMock(return_value=vectordb))
    monkeypatch.setattr(retriever, "BM25Search", lambda: fts)

    results = await hybrid_search("query", top_k=3)

    assert [r.chunk_id for r in results] == ["vec"]
    assert results[0].fallback_reason == "semantic_only_fallback"


@pytest.mark.asyncio
async def test_hybrid_search_raises_when_both_paths_fail(monkeypatch):
    _disable_optional_retrieval_features(monkeypatch)
    monkeypatch.setattr(
        retriever, "create_embedding", MagicMock(side_effect=RuntimeError("no embed"))
    )
    monkeypatch.setattr(
        retriever, "create_vectordb", AsyncMock(side_effect=RuntimeError("no vector"))
    )
    monkeypatch.setattr(
        retriever, "BM25Search", MagicMock(side_effect=RuntimeError("no bm25"))
    )

    with pytest.raises(RetrievalError):
        await hybrid_search("query", top_k=3)


@pytest.mark.asyncio
async def test_multi_query_preserves_keyword_when_semantic_branch_fails():
    embedding = MagicMock()
    embedding.embed_query = AsyncMock(side_effect=RuntimeError("embedding down"))
    fts = MagicMock()
    fts.search = AsyncMock(return_value=[
        TextSearchResult(
            chunk_id="kw", document_id="doc", text="keyword result", score=2.0
        )
    ])

    vector, text, semantic_failed, keyword_failed = await retriever._multi_search(
        ["original", "variant"], MagicMock(), fts, embedding, 3
    )

    assert vector == []
    assert [r.chunk_id for r in text] == ["kw"]
    assert semantic_failed is True
    assert keyword_failed is False


@pytest.mark.asyncio
async def test_hybrid_search_rerank_timeout_falls_back_to_rrf(monkeypatch):
    """rerank 超预算时必须降级返回 RRF 排序结果,标记 rerank_timeout,而不是抛错或返回空。"""
    _disable_optional_retrieval_features(monkeypatch)
    embedding = MagicMock()
    embedding.embed_query = AsyncMock(return_value=[0.1, 0.2])
    vector = VectorSearchResult(
        chunk_id="vec", document_id="doc", text="semantic result", score=0.9
    )
    keyword = TextSearchResult(
        chunk_id="kw", document_id="doc", text="keyword result", score=3.0
    )
    vectordb = MagicMock()
    vectordb.search = AsyncMock(return_value=[vector])
    fts = MagicMock()
    fts.search = AsyncMock(return_value=[keyword])

    monkeypatch.setattr(retriever, "create_embedding", lambda: embedding)
    monkeypatch.setattr(retriever, "create_vectordb", AsyncMock(return_value=vectordb))
    monkeypatch.setattr(retriever, "BM25Search", lambda: fts)
    monkeypatch.setattr(retriever.settings, "rerank_enabled", True)
    monkeypatch.setattr(retriever.settings, "rag_timeout_rerank", 0.05)

    async def slow_rerank(query, results, top_k):
        await asyncio.sleep(1.0)
        return results

    monkeypatch.setattr(retriever, "_rerank_results", slow_rerank)

    results = await hybrid_search("query", top_k=3, use_rerank=True)

    assert len(results) == 2  # RRF 结果保留,不为空
    assert all("rerank_timeout" in r.fallback_reason for r in results)


@pytest.mark.asyncio
async def test_hybrid_search_rerank_within_budget_no_fallback(monkeypatch):
    """rerank 在预算内完成时正常精排,无降级标记。"""
    _disable_optional_retrieval_features(monkeypatch)
    embedding = MagicMock()
    embedding.embed_query = AsyncMock(return_value=[0.1, 0.2])
    vector = VectorSearchResult(
        chunk_id="vec", document_id="doc", text="semantic result", score=0.9
    )
    vectordb = MagicMock()
    vectordb.search = AsyncMock(return_value=[vector])
    fts = MagicMock()
    fts.search = AsyncMock(return_value=[])

    monkeypatch.setattr(retriever, "create_embedding", lambda: embedding)
    monkeypatch.setattr(retriever, "create_vectordb", AsyncMock(return_value=vectordb))
    monkeypatch.setattr(retriever, "BM25Search", lambda: fts)
    monkeypatch.setattr(retriever.settings, "rerank_enabled", True)
    monkeypatch.setattr(retriever.settings, "rag_timeout_rerank", 5.0)

    async def fast_rerank(query, results, top_k):
        return results[:top_k]

    monkeypatch.setattr(retriever, "_rerank_results", fast_rerank)

    results = await hybrid_search("query", top_k=3, use_rerank=True)

    assert len(results) == 1
    assert "rerank" not in results[0].fallback_reason
