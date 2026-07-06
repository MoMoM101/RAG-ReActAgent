import asyncio
from qdrant_client import QdrantClient
from qdrant_client.models import Distance, VectorParams, PointStruct, Filter, FieldCondition, MatchValue

from config import settings
from .base import BaseVectorDB, VectorSearchResult


# ── Singleton client: all collections share one QdrantClient ──
_client: QdrantClient | None = None


def _get_client() -> QdrantClient:
    global _client
    if _client is None:
        if settings.qdrant_host:
            _client = QdrantClient(host=settings.qdrant_host, port=settings.qdrant_port)
        else:
            _client = QdrantClient(path=settings.qdrant_path)
    return _client


class QdrantVectorDB(BaseVectorDB):
    def __init__(self, collection_name: str | None = None):
        self.client = _get_client()
        self.collection = collection_name or (settings.qdrant_active_collection or settings.qdrant_collection)

    async def collection_exists(self) -> bool:
        collections = await asyncio.to_thread(self.client.get_collections)
        return any(c.name == self.collection for c in collections.collections)

    async def get_collection_dim(self) -> int | None:
        try:
            info = await asyncio.to_thread(self.client.get_collection, self.collection)
            if info and info.config and info.config.params:
                vp = info.config.params.vectors
                if hasattr(vp, "size"):
                    return vp.size
        except Exception:
            pass
        return None

    async def create_collection(self, vector_size: int) -> None:
        await asyncio.to_thread(
            self.client.create_collection,
            collection_name=self.collection,
            vectors_config=VectorParams(size=vector_size, distance=Distance.COSINE),
        )

    async def ensure_collection(self, vector_size: int) -> None:
        if await self.collection_exists():
            return
        try:
            await self.create_collection(vector_size)
        except Exception:
            if not await self.collection_exists():
                raise

    async def upsert(self, points: list[dict]) -> None:
        qdrant_points = [
            PointStruct(
                id=p["id"],
                vector=p["vector"],
                payload=p.get("payload", {}),
            )
            for p in points
        ]
        await asyncio.to_thread(
            self.client.upsert,
            collection_name=self.collection,
            points=qdrant_points,
        )

    async def search(self, vector: list[float], top_k: int = 10) -> list[VectorSearchResult]:
        results = await asyncio.to_thread(
            self.client.search,
            collection_name=self.collection,
            query_vector=vector,
            limit=top_k,
        )
        return [
            VectorSearchResult(
                chunk_id=r.id,
                document_id=r.payload.get("document_id", ""),
                text=r.payload.get("text", ""),
                score=r.score,
            )
            for r in results
        ]

    async def delete_by_document(self, document_id: str) -> None:
        await asyncio.to_thread(
            self.client.delete,
            collection_name=self.collection,
            points_selector=Filter(
                must=[FieldCondition(key="document_id", match=MatchValue(value=document_id))]
            ),
        )

    async def delete_by_chunks(self, chunk_ids: list[str]) -> None:
        if not chunk_ids:
            return
        await asyncio.to_thread(
            self.client.delete,
            collection_name=self.collection,
            points_selector=chunk_ids,
        )

    async def delete_by_ids(self, ids: list[str]) -> None:
        if not ids:
            return
        await asyncio.to_thread(
            self.client.delete,
            collection_name=self.collection,
            points_selector=ids,
        )
