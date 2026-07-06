from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass
class VectorSearchResult:
    chunk_id: str
    document_id: str
    text: str
    score: float


class BaseVectorDB(ABC):
    @abstractmethod
    async def upsert(self, points: list[dict]) -> None:
        """points: [{"id": str, "vector": list[float], "payload": dict}]"""
        ...

    @abstractmethod
    async def search(self, vector: list[float], top_k: int = 10) -> list[VectorSearchResult]:
        ...

    @abstractmethod
    async def delete_by_document(self, document_id: str) -> None:
        ...

    @abstractmethod
    async def delete_by_chunks(self, chunk_ids: list[str]) -> None:
        ...

    @abstractmethod
    async def delete_by_ids(self, ids: list[str]) -> None:
        """Delete points by their IDs."""
        ...

    @abstractmethod
    async def collection_exists(self) -> bool:
        ...

    @abstractmethod
    async def create_collection(self, vector_size: int) -> None:
        ...

    @abstractmethod
    async def ensure_collection(self, vector_size: int) -> None:
        """Create collection if not exists. Idempotent, safe for concurrent calls."""
        ...
