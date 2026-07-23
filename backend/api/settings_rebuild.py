"""Reusable helpers for collection rebuild operations."""

import asyncio
import logging
import os
import re
from collections.abc import Awaitable, Callable
from contextlib import suppress
from pathlib import Path

from config import settings

logger = logging.getLogger(__name__)


def derive_doc_key(doc_id: str, filename: str) -> str:
    """Derive a stable retrieval key without importing the RAG pipeline."""
    base = filename.rsplit(".", 1)[0] if "." in filename else filename
    return re.sub(r"[^a-zA-Z0-9-]", "-", base).strip("-").lower() or doc_id[:8]


async def cleanup_bm25_v2() -> None:
    """Remove residual BM25 double-buffer rebuild tables."""
    from sqlalchemy import text as sa_text

    from models.database import engine

    async with engine.begin() as conn:
        for table in (
            "bm25_docs_v2",
            "bm25_index_v2",
            "bm25_stats_v2",
            "chunks_fts_v2",
        ):
            await conn.execute(sa_text(f"DROP TABLE IF EXISTS {table}"))


async def switch_bm25_tables() -> None:
    """Atomically replace live BM25 tables with a validated v2 buffer."""
    from sqlalchemy import text as sa_text

    from models.database import engine

    async with engine.begin() as conn:
        table_names = ("bm25_docs", "bm25_index", "bm25_stats")
        result = await conn.execute(
            sa_text(
                "SELECT name FROM sqlite_master "
                "WHERE type = 'table' AND name IN "
                "('bm25_docs_v2', 'bm25_index_v2', 'bm25_stats_v2')"
            )
        )
        staged = {row[0] for row in result.fetchall()}
        missing = [f"{name}_v2" for name in table_names if f"{name}_v2" not in staged]
        if missing:
            raise RuntimeError(f"BM25 staged tables missing: {', '.join(missing)}")

        live_result = await conn.execute(
            sa_text(
                "SELECT name FROM sqlite_master "
                "WHERE type = 'table' AND name IN "
                "('bm25_docs', 'bm25_index', 'bm25_stats')"
            )
        )
        live = {row[0] for row in live_result.fetchall()}

        for index_name in (
            "idx_bm25_docs_did",
            "idx_bm25_index_term",
            "idx_bm25_docs_v2_did",
            "idx_bm25_index_v2_term",
        ):
            await conn.execute(sa_text(f"DROP INDEX IF EXISTS {index_name}"))
        for name in table_names:
            await conn.execute(sa_text(f"DROP TABLE IF EXISTS {name}_old"))
            if name in live:
                await conn.execute(sa_text(f"ALTER TABLE {name} RENAME TO {name}_old"))
        for name in table_names:
            await conn.execute(sa_text(f"ALTER TABLE {name}_v2 RENAME TO {name}"))
        for name in table_names:
            await conn.execute(sa_text(f"DROP TABLE IF EXISTS {name}_old"))
        await conn.execute(
            sa_text("CREATE INDEX idx_bm25_docs_did ON bm25_docs(document_id)")
        )
        await conn.execute(
            sa_text("CREATE INDEX idx_bm25_index_term ON bm25_index(term)")
        )


async def get_sample_text() -> str | None:
    """Read the longest available document text for embedding preflight."""
    from sqlalchemy import select

    from models.database import session_scope
    from models.orm import Document

    async with session_scope() as session:
        result = await session.execute(
            select(Document.raw_text)
            .where(Document.raw_text.isnot(None))
            .where(Document.raw_text != "")
            .order_by(Document.file_size.desc())
            .limit(1)
        )
        row = result.first()
        if row and row[0]:
            return row[0]

    from textdb.bm25_search import BM25Search

    fts = BM25Search()
    try:
        rows = await fts.raw_query(
            "SELECT text FROM bm25_docs ORDER BY length(text) DESC LIMIT 1"
        )
        if rows and rows[0]:
            return re.sub(r"\s+(?=[一-鿿㐀-䶿豈-﫿])", "", rows[0][0]).strip()
    except Exception:
        pass
    return None


async def preflight_chunk_size(sample_text: str, initial_size: int, embedding) -> int:
    """Reduce chunk size until the embedding provider accepts the sample."""
    from rag.splitter import split_text

    chunk_size = initial_size
    for _attempt in range(10):
        chunks = split_text(sample_text, chunk_size, settings.chunk_overlap)
        if not chunks:
            return chunk_size
        longest = max(chunks, key=lambda chunk: len(chunk.text))
        try:
            await embedding.embed_query(longest.text)
            return chunk_size
        except Exception as exc:
            error = str(exc)[:300].lower()
            is_length_error = (
                "too long" in error
                or "too many token" in error
                or "maximum context" in error
                or "context length" in error
                or "token limit" in error
                or ("reduce" in error and "length" in error)
            )
            if not is_length_error:
                raise
            new_size = max(chunk_size * 2 // 3, 8)
            logger.warning(
                "preflight chunk_size too large current=%d next=%d",
                chunk_size,
                new_size,
            )
            chunk_size = new_size

    raise RuntimeError(
        f"Pre-flight failed after 10 rounds (final chunk_size={chunk_size})"
    )


def _write_pointer(path: Path, pointers: dict) -> None:
    """Atomically persist active collection pointers."""
    import json

    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(pointers), encoding="utf-8")
    os.replace(temporary, path)


async def activate_qdrant_and_bm25(
    new_vdb,
    new_name: str,
    old_name: str,
    pointer_path: Path,
    switch_bm25: Callable[[], Awaitable[None]],
) -> None:
    """Switch vector/BM25 indexes and restore the old vector pointer on failure."""
    import json

    original_bytes = pointer_path.read_bytes() if pointer_path.exists() else None
    pointers = json.loads(original_bytes) if original_bytes else {}
    previous_runtime = settings.qdrant_active_collection

    pointers["rag_chunks"] = new_name
    _write_pointer(pointer_path, pointers)
    settings.qdrant_active_collection = new_name
    try:
        await switch_bm25()
    except BaseException:
        settings.qdrant_active_collection = previous_runtime
        if original_bytes is None:
            pointer_path.unlink(missing_ok=True)
        else:
            restore_path = pointer_path.with_suffix(pointer_path.suffix + ".restore")
            restore_path.write_bytes(original_bytes)
            os.replace(restore_path, pointer_path)
        with suppress(Exception):
            new_vdb.client.delete_collection(new_name)
        raise

    if old_name != new_name:
        old_vdb = type(new_vdb)(collection_name=old_name)
        if await old_vdb.collection_exists():
            old_vdb.client.delete_collection(old_name)

    all_collections = await asyncio.to_thread(new_vdb.client.get_collections)
    for collection in all_collections.collections:
        if (
            collection.name == settings.qdrant_collection
            or collection.name.startswith(settings.qdrant_collection + "_")
        ) and collection.name != new_name:
            new_vdb.client.delete_collection(collection.name)
