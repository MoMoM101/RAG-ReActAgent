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


def _is_all_cjk(s: str) -> bool:
    """Check if all characters in string are CJK Unified Ideographs."""
    return bool(s) and all("一" <= c <= "鿿" for c in s)


def _tokenize_query(query: str) -> tuple[list[str], list[str]]:
    """Tokenize query with jieba. Merge consecutive short CJK tokens into >=3 char terms
    so FTS5 trigram tokenizer can index them. Returns (match_terms, like_terms):
    - match_terms: words >= 3 chars, used for FTS5 MATCH OR (BM25 scored)
    - like_terms: words <= 2 chars, used for LIKE fallback (fixed 0.3 score)
    """
    import jieba

    words = [w.strip() for w in jieba.cut(query) if w.strip()]
    if not words:
        return [], []

    # Merge consecutive short (<=2) all-CJK tokens to reach >=3 chars
    merged: list[str] = []
    buf = ""
    for w in words:
        all_cjk = _is_all_cjk(w)
        short = len(w) <= 2
        if short and all_cjk:
            buf += w
            if len(buf) >= 3:
                merged.append(buf)
                buf = ""
        else:
            if buf:
                merged.append(buf)
                buf = ""
            merged.append(w)
    if buf:
        merged.append(buf)

    match_terms = [w for w in merged if len(w) >= 3]
    like_terms = [w for w in merged if len(w) <= 2]
    return match_terms, like_terms


class SQLiteFTS5(BaseTextDB):
    TABLE = "chunks_fts"

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
        escaped = _escape_sql(text)
        await self._exec(
            f"INSERT INTO {self.TABLE} (chunk_id, document_id, content) "
            f"VALUES ('{cid}', '{did}', '{escaped}')"
        )

    @staticmethod
    def _escape_fts5(query: str) -> str:
        """Remove FTS5 special characters. Hyphens and dots are replaced with spaces
        since FTS5 treats them as column-filter separators."""
        cleaned = re.sub(r'["*^()]', "", query)
        cleaned = cleaned.replace("-", " ")
        return cleaned.replace(".", " ")

    async def search(
        self, query: str, top_k: int = 10, document_id: str = ""
    ) -> list[TextSearchResult]:
        fts5_safe = self._escape_fts5(query)
        query_stripped = fts5_safe.strip()

        # Ultra-short query (<=2 chars) — simple LIKE fallback
        if len(query_stripped) <= 2:
            escaped = _escape_sql(query_stripped)
            sql = (
                f"SELECT chunk_id, document_id, content, 0.5 as score "
                f"FROM {self.TABLE} WHERE content LIKE '%{escaped}%'"
            )
            if document_id:
                sql += f" AND document_id = '{_safe_id(document_id)}'"
            sql += f" LIMIT {top_k}"
            rows = await self._query(sql)
            return [
                TextSearchResult(
                    chunk_id=row[0], document_id=row[1], text=row[2],
                    score=float(row[3]) if row[3] is not None else 0.0,
                )
                for row in rows
            ]

        # Tokenize with jieba → split into MATCH terms and LIKE fallback terms
        match_terms, like_terms = _tokenize_query(query_stripped)
        all_rows: list[tuple] = []

        # Primary: FTS5 MATCH with OR for >=3 char terms (BM25 scored)
        if match_terms:
            or_query = " OR ".join(match_terms)
            escaped_or = _escape_sql(or_query)
            sql = (
                f"SELECT chunk_id, document_id, content, bm25({self.TABLE}) as score "
                f"FROM {self.TABLE} WHERE {self.TABLE} MATCH '{escaped_or}'"
            )
            if document_id:
                sql += f" AND document_id = '{_safe_id(document_id)}'"
            sql += f" ORDER BY score LIMIT {top_k * 3}"
            all_rows = await self._query(sql)

        # Secondary: LIKE fallback for <=2 char terms (fixed 0.3 score)
        if like_terms:
            like_clauses = " OR ".join(
                f"content LIKE '%{_escape_sql(w)}%'" for w in like_terms
            )
            sql = (
                f"SELECT chunk_id, document_id, content, 0.3 as score "
                f"FROM {self.TABLE} WHERE ({like_clauses})"
            )
            if document_id:
                sql += f" AND document_id = '{_safe_id(document_id)}'"
            sql += f" LIMIT {top_k}"
            like_rows = await self._query(sql)
            existing_ids = {r[0] for r in all_rows}
            for row in like_rows:
                if row[0] not in existing_ids:
                    all_rows.append(row)

        # Fallback: if both paths returned nothing (e.g. all terms were short and
        # LIKE found nothing), do a broad LIKE with the full query
        if not all_rows:
            escaped_full = _escape_sql(query_stripped)
            sql = (
                f"SELECT chunk_id, document_id, content, 0.2 as score "
                f"FROM {self.TABLE} WHERE content LIKE '%{escaped_full}%'"
            )
            if document_id:
                sql += f" AND document_id = '{_safe_id(document_id)}'"
            sql += f" LIMIT {top_k}"
            all_rows = await self._query(sql)

        # Sort by BM25 score descending, dedup, limit
        all_rows.sort(key=lambda r: r[3] if r[3] is not None else 0.0, reverse=True)
        return [
            TextSearchResult(
                chunk_id=row[0], document_id=row[1], text=row[2],
                score=float(row[3]) if row[3] is not None else 0.0,
            )
            for row in all_rows[:top_k]
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
