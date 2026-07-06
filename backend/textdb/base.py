from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass
class TextSearchResult:
    chunk_id: str
    document_id: str
    text: str
    score: float  # BM25 score


class BaseTextDB(ABC):
    @abstractmethod
    async def insert(self, chunk_id: str, document_id: str, text: str) -> None: ...

    @abstractmethod
    async def search(self, query: str, top_k: int = 10) -> list[TextSearchResult]: ...

    @abstractmethod
    async def delete_by_document(self, document_id: str) -> None: ...

    @abstractmethod
    async def delete_by_chunks(self, chunk_ids: list[str]) -> None: ...

    @abstractmethod
    async def count(self) -> int: ...
