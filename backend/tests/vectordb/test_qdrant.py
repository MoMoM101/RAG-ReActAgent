import pytest

from config import settings


def _qdrant_available() -> bool:
    """Check if Qdrant is available (local path mode or remote host)."""
    if not settings.qdrant_host:
        return True  # local path mode, always available
    try:
        import socket
        s = socket.create_connection((settings.qdrant_host, settings.qdrant_port), timeout=2)
        s.close()
        return True
    except (OSError, ConnectionRefusedError):
        return False


qdrant_required = pytest.mark.skipif(
    not _qdrant_available(),
    reason="Qdrant is not available. For remote mode, start with: docker run -p 6333:6333 qdrant/qdrant",
)


@pytest.mark.asyncio
@qdrant_required
async def test_upsert_and_search():
    from vectordb.qdrant import QdrantVectorDB

    db = QdrantVectorDB()
    dim = settings.embedding_dim
    if not await db.collection_exists():
        await db.create_collection(dim)

    chunk_a = "550e8400-e29b-41d4-a716-446655440001"
    chunk_b = "550e8400-e29b-41d4-a716-446655440002"

    vec_a = [1.0] + [0.0] * (dim - 1)
    vec_b = [0.0, 1.0] + [0.0] * (dim - 2)
    query_vec = [1.0, 0.1] + [0.0] * (dim - 2)

    await db.upsert([
        {
            "id": chunk_a,
            "vector": vec_a,
            "payload": {"document_id": "doc_1", "text": "部署流程说明"},
        },
        {
            "id": chunk_b,
            "vector": vec_b,
            "payload": {"document_id": "doc_1", "text": "数据库配置"},
        },
    ])

    results = await db.search(query_vec, top_k=2)
    assert len(results) == 2
    assert results[0].chunk_id == chunk_a

    await db.delete_by_chunks([chunk_a, chunk_b])
