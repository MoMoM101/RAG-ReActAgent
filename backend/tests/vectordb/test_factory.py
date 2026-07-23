from unittest.mock import AsyncMock, MagicMock, patch

from config import settings
from vectordb.factory import create_vectordb
from vectordb.qdrant import QdrantVectorDB


async def test_factory_uses_race_safe_collection_initialization(monkeypatch):
    # create_vectordb caches the detected dimension globally; isolate that
    # intentional side effect so later Qdrant tests keep their configured size.
    monkeypatch.setattr(settings, "embedding_dim", settings.embedding_dim)
    embedding = AsyncMock()
    embedding.embed_query.return_value = [0.1, 0.2, 0.3]
    database = AsyncMock()
    database.get_collection_dim.return_value = 3

    with (
        patch("embedding.factory.create_embedding", return_value=embedding),
        patch("vectordb.factory.QdrantVectorDB", return_value=database),
    ):
        result = await create_vectordb()

    assert result is database
    database.ensure_collection.assert_awaited_once_with(3)
    database.create_collection.assert_not_awaited()


async def test_upsert_retries_transient_qdrant_replica_error():
    database = object.__new__(QdrantVectorDB)
    database.client = MagicMock()
    database.collection = "rag_chunks"
    transient = RuntimeError(
        "Unexpected Response: 500 (Internal Server Error): "
        "Failed to apply operation to Active replica"
    )

    with (
        patch("vectordb.qdrant._call", new=AsyncMock(side_effect=[transient, None])) as call,
        patch("vectordb.qdrant.asyncio.sleep", new=AsyncMock()) as sleep,
    ):
        await database.upsert(
            [{"id": "00000000-0000-0000-0000-000000000001", "vector": [0.1]}]
        )

    assert call.await_count == 2
    sleep.assert_awaited_once_with(0.25)
