from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass
class TextSearchResult:
    chunk_id: str
    document_id: str
    text: str
    score: float  # BM25 score
    document_key: str = ""
    section_key: str = ""


class BaseTextDB(ABC):
    @abstractmethod
    async def insert(self, chunk_id: str, document_id: str, text: str,
                     document_key: str = "", section_key: str = "",
                     chunk_index: int = 0) -> None: ...

    @abstractmethod
    async def search(self, query: str, top_k: int = 10, document_id: str = "") -> list[TextSearchResult]: ...

    @abstractmethod
    async def delete_by_document(self, document_id: str) -> None: ...

    @abstractmethod
    async def delete_by_chunks(self, chunk_ids: list[str]) -> None: ...

    @abstractmethod
    async def count(self) -> int: ...

    @abstractmethod
    async def get_chunk_ids_by_document(self, document_id: str) -> list[str]:
        """Return all chunk_ids for a given document_id."""
        ...
