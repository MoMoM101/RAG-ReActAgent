from abc import ABC, abstractmethod


class BaseReranker(ABC):
    @abstractmethod
    async def rerank(self, query: str, documents: list[str]) -> list[tuple[int, float]]:
        """Rerank documents against query.

        Returns list of (original_index, score) sorted by score descending.
        """
        ...
