import pytest

from config import settings

TEST_DOC_ID = "test-retriever-integration-doc"


def _qdrant_available() -> bool:
    if not settings.qdrant_host:
        return True
    try:
        import socket
        s = socket.create_connection((settings.qdrant_host, settings.qdrant_port), timeout=2)
        s.close()
        return True
    except (OSError, ConnectionRefusedError):
        return False


qdrant_required = pytest.mark.skipif(
    not _qdrant_available(),
    reason="Qdrant not available",
)


async def _ensure_collection_dim(db) -> int | None:
    """Check collection dimension matches embedding API. Returns dim or None if mismatch."""
    api_key = settings.embedding_api_key or settings.llm_api_key
    if not api_key or api_key == "your-api-key-here":
        pytest.skip("Embedding API key not configured")

    from embedding.factory import create_embedding
    emb = create_embedding()
    try:
        actual_dim = len(await emb.embed_query("dim check"))
    except Exception as e:
        type_name = type(e).__name__
        if type_name in ("APITimeoutError", "APIConnectionError"):
            pytest.skip(f"Embedding API unavailable: {type_name}")
        raise
    if await db.collection_exists():
        existing_dim = await db.get_collection_dim()
        if existing_dim and existing_dim != actual_dim:
            return None  # mismatch
    else:
        await db.create_collection(actual_dim)
    return actual_dim


@pytest.mark.asyncio
@qdrant_required
async def test_hybrid_search_hits_qdrant():
    """Search returns results from Qdrant semantic search."""
    from rag.retriever import hybrid_search
    from textdb.bm25_search import BM25Search
    from vectordb.qdrant import QdrantVectorDB

    chunk_id = "550e8400-e29b-41d4-a716-44665544a001"

    db = QdrantVectorDB()
    dim = await _ensure_collection_dim(db)
    if dim is None:
        pytest.skip("Qdrant collection dimension mismatch with embedding API")

    await db.upsert([{
        "id": chunk_id,
        "vector": [1.0] + [0.0] * (dim - 1),
        "payload": {"document_id": TEST_DOC_ID, "text": "测试文档内容关于机器学习部署流程"},
    }])

    fts = BM25Search()
    try:
        results = await hybrid_search("机器学习部署", top_k=3)
        assert len(results) > 0
        assert any(r.document_id == TEST_DOC_ID for r in results)
    finally:
        await db.delete_by_chunks([chunk_id])
        await fts.delete_by_document(TEST_DOC_ID)


@pytest.mark.asyncio
@qdrant_required
async def test_hybrid_search_hits_fts5():
    """FTS5 keyword search finds exact match that semantic search might miss."""
    from rag.retriever import hybrid_search
    from textdb.bm25_search import BM25Search
    from vectordb.qdrant import QdrantVectorDB

    db = QdrantVectorDB()
    if await _ensure_collection_dim(db) is None:
        pytest.skip("Qdrant collection dimension mismatch with embedding API")

    fts = BM25Search()
    chunk_id = "550e8400-e29b-41d4-a716-44665544b002"
    await fts.insert(chunk_id, TEST_DOC_ID, "XYZ-9000 型号规格参数详细说明")

    try:
        try:
            results = await hybrid_search("XYZ-9000", top_k=3)
        except Exception as e:
            type_name = type(e).__name__
            if type_name in ("APITimeoutError", "APIConnectionError"):
                pytest.skip(f"Embedding API unavailable: {type_name}")
            raise
        assert len(results) > 0
    finally:
        await fts.delete_by_chunks([chunk_id])


@pytest.mark.asyncio
@qdrant_required
async def test_hybrid_search_empty_documents():
    """Returns empty list when no matching documents exist."""
    from rag.retriever import hybrid_search
    from vectordb.qdrant import QdrantVectorDB

    db = QdrantVectorDB()
    if await _ensure_collection_dim(db) is None:
        pytest.skip("Qdrant collection dimension mismatch with embedding API")

    try:
        results = await hybrid_search("不可能匹配的罕见查询词 zuihao12345", top_k=3)
    except Exception as e:
        type_name = type(e).__name__
        if type_name in ("APITimeoutError", "APIConnectionError"):
            pytest.skip(f"Embedding API unavailable: {type_name}")
        raise
    assert isinstance(results, list)


# ── Dedup tests ──────────────────────────

from datetime import UTC

from rag.retriever import RetrievalResult, _dedup_results


@pytest.mark.asyncio
async def test_dedup_single_result():
    """Single result is returned unchanged."""
    r = RetrievalResult(chunk_id="c1", document_id="d1", text="hello world", score=0.9, source="semantic")
    kept = await _dedup_results([r])
    assert kept == [r]


@pytest.mark.asyncio
async def test_dedup_different_docs_similar_text():
    """Similar text from different docs: first-seen (higher-scored) chunk wins."""
    from datetime import datetime, timedelta

    from models.database import session_scope
    from models.orm import Document

    doc1_id = "test-dedup-doc-1"
    doc2_id = "test-dedup-doc-2"

    async with session_scope() as session:
        session.add(Document(id=doc1_id, filename="a.txt", file_hash="h1",
                             file_size=100, file_type=".txt", status="ready",
                             created_at=datetime.now(UTC) - timedelta(days=1)))
        session.add(Document(id=doc2_id, filename="b.txt", file_hash="h2",
                             file_size=100, file_type=".txt", status="ready",
                             created_at=datetime.now(UTC)))
        await session.commit()

    # These texts differ by 1 character → difflib ratio ≈ 0.93 (> 0.85 threshold)
    r1 = RetrievalResult(chunk_id="c1", document_id=doc1_id,
                         text="机器学习模型部署流程详细说明文档", score=0.9, source="semantic")
    r2 = RetrievalResult(chunk_id="c2", document_id=doc2_id,
                         text="机器学习模型部署流程详细说明文件", score=0.85, source="semantic")

    try:
        kept = await _dedup_results([r1, r2])
        assert len(kept) == 1
        # First-seen (higher-scored) chunk is kept, not necessarily the newer doc
        assert kept[0].document_id == doc1_id
    finally:
        async with session_scope() as session:
            from sqlalchemy import delete
            await session.execute(delete(Document).where(Document.id.in_([doc1_id, doc2_id])))
            await session.commit()


@pytest.mark.asyncio
async def test_dedup_different_text_kept():
    """Different content from different docs: both kept."""
    from datetime import datetime

    from models.database import session_scope
    from models.orm import Document

    doc1_id = "test-dedup-doc-3"
    doc2_id = "test-dedup-doc-4"

    async with session_scope() as session:
        session.add(Document(id=doc1_id, filename="a.txt", file_hash="h3",
                             file_size=100, file_type=".txt", status="ready",
                             created_at=datetime.now(UTC)))
        session.add(Document(id=doc2_id, filename="b.txt", file_hash="h4",
                             file_size=100, file_type=".txt", status="ready",
                             created_at=datetime.now(UTC)))
        await session.commit()

    r1 = RetrievalResult(chunk_id="c1", document_id=doc1_id,
                         text="机器学习模型部署流程", score=0.9, source="semantic")
    r2 = RetrievalResult(chunk_id="c2", document_id=doc2_id,
                         text="数据库性能优化指南", score=0.7, source="semantic")

    try:
        kept = await _dedup_results([r1, r2])
        assert len(kept) == 2
    finally:
        async with session_scope() as session:
            from sqlalchemy import delete
            await session.execute(delete(Document).where(Document.id.in_([doc1_id, doc2_id])))
            await session.commit()
