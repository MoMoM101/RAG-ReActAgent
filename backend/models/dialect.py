# backend/models/dialect.py
"""Database dialect capability adapter.

Centralizes SQLite-specific behavior so that PostgreSQL support can be
added later without changing business logic.  Unsupported dialects raise
UnsupportedDialectError immediately — no silent fallback.
"""
from typing import Protocol

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import text


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
        """Rebuild the FTS5 index from the bm25_docs table."""
        await session.execute(text("DROP TABLE IF EXISTS bm25_docs"))
        await session.execute(text("""
            CREATE VIRTUAL TABLE bm25_docs
            USING fts5(
                doc_id,
                document_id,
                chunk_id,
                text,
                tokenize='porter unicode61'
            )
        """))
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
