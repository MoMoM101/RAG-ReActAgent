from rag.retriever import _rrf_fusion
from vectordb.base import VectorSearchResult
from textdb.base import TextSearchResult


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
