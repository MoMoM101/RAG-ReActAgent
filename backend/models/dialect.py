# backend/models/dialect.py
"""Database dialect capability adapter.

Centralizes SQLite-specific behavior so that PostgreSQL support can be
added later without changing business logic.  Unsupported dialects raise
UnsupportedDialectError immediately — no silent fallback.
"""
from typing import Protocol

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


class UnsupportedDialectError(RuntimeError):
    """Raised when the database dialect is not supported."""


class DatabaseCapabilities(Protocol):
    dialect_name: str
    supports_fts: bool

    async def health_check(self, session: AsyncSession) -> None: ...
    async def rebuild_fts(self, session: AsyncSession) -> None: ...


class SqliteAdapter:
    dialect_name = "sqlite"
    supports_fts = True

    async def health_check(self, session: AsyncSession) -> None:
        """Verify SQLite is responsive and WAL mode is active."""
        result = await session.execute(text("PRAGMA journal_mode"))
        row = result.fetchone()
        if not row or row[0] != "wal":
            raise RuntimeError(
                f"SQLite journal_mode is '{row[0] if row else 'unknown'}', expected 'wal'. "
                "The filesystem may not support WAL."
            )

    async def rebuild_fts(self, session: AsyncSession) -> None:
        """Recreate the current BM25 schema and the separate legacy FTS table.

        ``bm25_docs`` is a relational document table used by ``BM25Search``.
        It must never share a name with an FTS5 virtual table.
        """
        for table in ("bm25_index", "bm25_stats", "bm25_docs", "chunks_fts"):
            await session.execute(text(f"DROP TABLE IF EXISTS {table}"))

        statements = (
            """
            CREATE TABLE bm25_docs (
                chunk_id TEXT PRIMARY KEY,
                document_id TEXT NOT NULL,
                document_key TEXT NOT NULL DEFAULT '',
                section_key TEXT NOT NULL DEFAULT '',
                chunk_index INTEGER NOT NULL DEFAULT 0,
                text TEXT NOT NULL,
                token_count INTEGER NOT NULL DEFAULT 0
            )
            """,
            """
            CREATE TABLE bm25_index (
                term TEXT NOT NULL,
                chunk_id TEXT NOT NULL,
                tf INTEGER NOT NULL,
                PRIMARY KEY (term, chunk_id)
            )
            """,
            """
            CREATE TABLE bm25_stats (
                term TEXT PRIMARY KEY,
                df INTEGER NOT NULL DEFAULT 0
            )
            """,
            """
            CREATE VIRTUAL TABLE chunks_fts
            USING fts5(chunk_id, document_id, content, tokenize='trigram')
            """,
            "CREATE INDEX idx_bm25_docs_did ON bm25_docs(document_id)",
            "CREATE INDEX idx_bm25_index_term ON bm25_index(term)",
        )
        for statement in statements:
            await session.execute(text(statement))
        await session.commit()


def get_adapter(dialect_name: str) -> DatabaseCapabilities:
    """Return the capability adapter for a given dialect.

    Raises UnsupportedDialectError if the dialect is not supported.
    """
    if dialect_name == "sqlite":
        return SqliteAdapter()
    raise UnsupportedDialectError(
        f"Database dialect '{dialect_name}' is not supported. "
        "Only 'sqlite' is supported in this release."
    )
