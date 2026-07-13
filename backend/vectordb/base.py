from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass
class VectorSearchResult:
    chunk_id: str
    document_id: str
    text: str
    score: float
    document_key: str = ""
    section_key: str = ""


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

    @abstractmethod
    async def get_chunk_ids_by_document(self, document_id: str) -> list[str]:
        """Return all chunk_ids for a given document_id."""
        ...
