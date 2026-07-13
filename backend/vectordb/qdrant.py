import asyncio
import logging
from contextlib import suppress

from qdrant_client import QdrantClient
from qdrant_client.models import (
    Distance,
    FieldCondition,
    Filter,
    MatchValue,
    PointStruct,
    SearchParams,
    VectorParams,
)

from config import settings

from .base import BaseVectorDB, VectorSearchResult

logger = logging.getLogger(__name__)

# ── Singleton client: all collections share one QdrantClient ──
_client: QdrantClient | None = None
_client_path: str = ""
_client_healthy: bool = False


def _get_client_key() -> str:
    """Return a key identifying the current Qdrant connection target."""
    if settings.qdrant_host:
        return f"{settings.qdrant_host}:{settings.qdrant_port}"
    return settings.qdrant_path


def _get_client() -> QdrantClient:
    global _client, _client_path, _client_healthy
    current_key = _get_client_key()

    if _client is not None and _client_path == current_key and _client_healthy:
        return _client

    # Path changed or client unhealthy — close old, create new
    if _client is not None:
        with suppress(Exception):
            _client.close()

    if settings.qdrant_host:
        _client = QdrantClient(host=settings.qdrant_host, port=settings.qdrant_port)
    else:
        _client = QdrantClient(path=settings.qdrant_path)

    _client_path = current_key
    _client_healthy = True
    logger.info("Qdrant client created path=%s", current_key)
    return _client


def reset_client_for_test() -> None:
    """Force next _get_client() call to create a new connection. Test only."""
    global _client, _client_path, _client_healthy
    if _client is not None:
        with suppress(Exception):
            _client.close()
    _client = None
    _client_path = ""
    _client_healthy = False


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
                if vp is not None and hasattr(vp, "size"):
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
        try:
            await asyncio.to_thread(
                self.client.upsert,
                collection_name=self.collection,
                points=qdrant_points,
            )
        except Exception:
            global _client_healthy
            _client_healthy = False
            raise

    async def search(self, vector: list[float], top_k: int = 10) -> list[VectorSearchResult]:
        try:
            response = await asyncio.to_thread(
                self.client.query_points,
                collection_name=self.collection,
                query=vector,
                limit=top_k,
                search_params=SearchParams(hnsw_ef=128),
            )
        except Exception:
            global _client_healthy
            _client_healthy = False
            raise
        return [
            VectorSearchResult(
                chunk_id=str(r.id),
                document_id=(r.payload or {}).get("document_id", ""),
                text=(r.payload or {}).get("text", ""),
                score=r.score,
                document_key=(r.payload or {}).get("document_key", ""),
                section_key=(r.payload or {}).get("section_key", ""),
            )
            for r in response.points
        ]

    async def get_chunk_ids_by_document(self, document_id: str) -> list[str]:
        """Return all chunk_ids for a given document_id from Qdrant."""
        ids: list[str] = []
        offset = None
        while True:
            points, next_offset = await asyncio.to_thread(
                self.client.scroll,
                collection_name=self.collection,
                scroll_filter=Filter(
                    must=[FieldCondition(
                        key="document_id",
                        match=MatchValue(value=document_id),
                    )]
                ),
                limit=1000,
                offset=offset,
                with_payload=False,
            )
            ids.extend(str(p.id) for p in points)
            if next_offset is None:
                break
            offset = next_offset
        return ids

    async def delete_by_document(self, document_id: str) -> None:
        try:
            await asyncio.to_thread(
                self.client.delete,
                collection_name=self.collection,
                points_selector=Filter(
                    must=[FieldCondition(key="document_id", match=MatchValue(value=document_id))]
                ),
            )
        except Exception:
            global _client_healthy
            _client_healthy = False
            raise

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
