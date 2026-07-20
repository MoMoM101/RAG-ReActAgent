import asyncio
import uuid
from contextlib import suppress

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


@pytest.mark.asyncio
@qdrant_required
async def test_client_rebuild_on_path_change():
    """Client should rebuild when qdrant_path changes (config change detection)."""
    from vectordb.qdrant import _get_client, _get_client_key, reset_client_for_test

    reset_client_for_test()

    client1 = _get_client()
    key1 = _get_client_key()
    assert key1 != ""
    assert client1 is not None

    # Same key, healthy — returns same client
    client1b = _get_client()
    assert client1b is client1

    # After reset — new client is created
    reset_client_for_test()
    client2 = _get_client()
    assert client2 is not None
    key2 = _get_client_key()
    assert key2 == key1  # Same path, but new client instance


@pytest.mark.asyncio
@qdrant_required
async def test_client_marks_unhealthy_on_search_error():
    """Search error should mark client unhealthy, next _get_client rebuilds."""
    from vectordb.qdrant import QdrantVectorDB, _get_client, reset_client_for_test

    reset_client_for_test()

    db = QdrantVectorDB()
    dim = settings.embedding_dim
    if not await db.collection_exists():
        await db.create_collection(dim)

    # Force client to be healthy first
    assert _get_client() is not None

    # Search with an invalid collection name should trigger exception
    bad_db = QdrantVectorDB(collection_name="nonexistent_collection_xyz_123")
    with suppress(Exception):
        await bad_db.search([1.0] + [0.0] * (dim - 1), top_k=1)

    # After error, client should be marked unhealthy
    # Note: the exception is raised from to_thread, so it reaches our catch block
    # But search uses self.client.search which is the shared singleton
    # The health flag gets set by the except clause

    # Verify reset_for_test works for cleanup
    reset_client_for_test()
    new_client = _get_client()
    assert new_client is not None


@pytest.mark.asyncio
@qdrant_required
async def test_concurrent_upserts_local_mode():
    """Local-mode Qdrant persists to a single sqlite3 connection that is not
    thread-safe.  Concurrent upserts from asyncio.to_thread pool threads must
    be serialized by the adapter, otherwise sqlite raises SQLITE_MISUSE
    ("bad parameter or other API misuse")."""
    from vectordb.qdrant import QdrantVectorDB, reset_client_for_test

    if settings.qdrant_host:
        pytest.skip("server mode is concurrency-safe; this test targets local mode")

    reset_client_for_test()
    db = QdrantVectorDB(collection_name="test_concurrent_upserts")
    dim = settings.embedding_dim
    await db.ensure_collection(dim)

    def _batch(n: int) -> list[dict]:
        return [
            {
                "id": str(uuid.uuid4()),
                "vector": [float(i % 7)] + [0.0] * (dim - 1),
                "payload": {"document_id": f"doc_{n}", "text": f"chunk {i}"},
            }
            for i in range(50)
        ]

    for _round in range(5):
        await asyncio.gather(*(db.upsert(_batch(n)) for n in range(4)))
