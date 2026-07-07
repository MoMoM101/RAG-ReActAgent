import re
from typing import Any

from models.database import async_session

from .base import BaseTextDB, TextSearchResult


def _escape_sql(s: str) -> str:
    """Escape single quotes for SQL string literals."""
    return s.replace("'", "''")


def _safe_id(s: str) -> str:
    """Validate identifier-like string. Only allows alphanumeric, hyphens, underscores."""
    if not re.fullmatch(r"[a-zA-Z0-9\-_]+", s):
        raise ValueError(f"Invalid identifier: {s}")
    return s


class SQLiteFTS5(BaseTextDB):
    TABLE = "chunks_fts"

    @staticmethod
    def _segment_cjk(text: str) -> str:
        """Insert spaces around CJK characters for FTS5 unicode61 tokenizer."""
        return re.sub(r"([一-鿿㐀-䶿豈-﫿])", r" \1 ", text)

    async def _exec(self, sql: str) -> None:
        """Execute raw SQL via driver-level connection (avoids bind-param parsing)."""
        async with async_session() as session:
            conn = await session.connection()
            await conn.exec_driver_sql(sql)
            await session.commit()

    async def _query(self, sql: str) -> list[Any]:
        async with async_session() as session:
            conn = await session.connection()
            result = await conn.exec_driver_sql(sql)
            return list(result.fetchall())

    async def insert(self, chunk_id: str, document_id: str, text: str) -> None:
        cid = _safe_id(chunk_id)
        did = _safe_id(document_id)
        segmented = _escape_sql(self._segment_cjk(text))
        await self._exec(
            f"INSERT INTO {self.TABLE} (chunk_id, document_id, content) "
            f"VALUES ('{cid}', '{did}', '{segmented}')"
        )

    @staticmethod
    def _escape_fts5(query: str) -> str:
        """Remove FTS5 special characters. Uses implicit AND matching (no phrase wrap).
        Hyphens are replaced with spaces since unicode61 tokenizer treats them as separators."""
        cleaned = re.sub(r'["*^()]', '', query)
        return cleaned.replace('-', ' ')

    async def search(self, query: str, top_k: int = 10, document_id: str = "") -> list[TextSearchResult]:
        fts5_safe = self._escape_fts5(query)
        segmented = _escape_sql(self._segment_cjk(fts5_safe))
        sql = (
            f"SELECT chunk_id, document_id, content, bm25({self.TABLE}) as score "
            f"FROM {self.TABLE} WHERE {self.TABLE} MATCH '{segmented}'"
        )
        if document_id:
            sql += f" AND document_id = '{_safe_id(document_id)}'"
        sql += f" ORDER BY score LIMIT {top_k}"
        rows = await self._query(sql)
        return [
            TextSearchResult(
                chunk_id=row[0],
                document_id=row[1],
                text=row[2],
                score=float(row[3]) if row[3] is not None else 0.0,
            )
            for row in rows
        ]

    async def delete_by_document(self, document_id: str) -> None:
        did = _safe_id(document_id)
        await self._exec(
            f"DELETE FROM {self.TABLE} WHERE document_id = '{did}'"
        )

    async def delete_by_chunks(self, chunk_ids: list[str]) -> None:
        if not chunk_ids:
            return
        safe_ids = [_safe_id(cid) for cid in chunk_ids]
        ids = "','".join(safe_ids)
        await self._exec(
            f"DELETE FROM {self.TABLE} WHERE chunk_id IN ('{ids}')"
        )

    async def count(self) -> int:
        rows = await self._query(f"SELECT COUNT(*) FROM {self.TABLE}")
        return rows[0][0] if rows else 0
