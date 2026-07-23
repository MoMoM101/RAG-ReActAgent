"""Clean up orphaned vectors in Qdrant and BM25 whose documents no longer exist in SQLite."""

import asyncio
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from config import settings
from vectordb.qdrant import QdrantVectorDB
from textdb.bm25_search import BM25Search
from models.database import session_scope
from sqlalchemy import text as sa_text

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("cleanup")


def _get_all_doc_ids_from_sqlite() -> set[str]:
    """Get all document IDs from the SQLite documents table."""
    import sqlite3
    db_url = settings.database_url
    db_path = db_url.replace("sqlite+aiosqlite:///", "")
    conn = sqlite3.connect(db_path)
    try:
        rows = conn.execute("SELECT id FROM documents").fetchall()
        return {row[0] for row in rows}
    finally:
        conn.close()


def _get_all_doc_ids_from_qdrant(vdb: QdrantVectorDB) -> set[str]:
    """Get all unique document_ids from Qdrant by scrolling all points."""
    doc_ids: set[str] = set()
    offset = None
    total = 0
    while True:
        points, next_offset = vdb.client.scroll(
            collection_name=vdb.collection,
            limit=1000,
            offset=offset,
            with_payload=True,
        )
        for p in points:
            did = (p.payload or {}).get("document_id", "")
            if did:
                doc_ids.add(did)
            total += 1
        if next_offset is None:
            break
        offset = next_offset
    logger.info("Qdrant collection=%s total_points=%d unique_doc_ids=%d",
                vdb.collection, total, len(doc_ids))
    return doc_ids


def _get_all_doc_ids_from_bm25() -> set[str]:
    """Get all unique document_ids from BM25 index."""
    import sqlite3
    db_url = settings.database_url
    db_path = db_url.replace("sqlite+aiosqlite:///", "")
    conn = sqlite3.connect(db_path)
    try:
        rows = conn.execute("SELECT DISTINCT document_id FROM bm25_docs").fetchall()
        return {row[0] for row in rows}
    except Exception:
        logger.info("BM25 bm25_docs table not found — skipping BM25 cleanup")
        return set()
    finally:
        conn.close()


async def cleanup_qdrant(vdb: QdrantVectorDB, orphan_ids: set[str]) -> int:
    """Delete orphan document vectors from Qdrant."""
    deleted = 0
    for doc_id in sorted(orphan_ids):
        try:
            chunk_ids = await vdb.get_chunk_ids_by_document(doc_id)
            if chunk_ids:
                await vdb.delete_by_document(doc_id)
                deleted += len(chunk_ids)
                logger.info("Qdrant: deleted %d chunks for orphan doc_id=%s", len(chunk_ids), doc_id)
        except Exception as e:
            logger.error("Qdrant: failed to delete orphan doc_id=%s: %s", doc_id, e)
    return deleted


def cleanup_bm25(orphan_ids: set[str]) -> int:
    """Delete orphan document entries from BM25 index."""
    import sqlite3
    db_url = settings.database_url
    db_path = db_url.replace("sqlite+aiosqlite:///", "")
    conn = sqlite3.connect(db_path)
    deleted = 0
    try:
        for doc_id in sorted(orphan_ids):
            # Delete from bm25_docs
            cur = conn.execute("DELETE FROM bm25_docs WHERE document_id = ?", (doc_id,))
            n = cur.rowcount
            # Delete from bm25_index (term -> doc entries)
            conn.execute("DELETE FROM bm25_index WHERE document_id = ?", (doc_id,))
            if n > 0:
                deleted += n
                logger.info("BM25: deleted %d entries for orphan doc_id=%s", n, doc_id)
        conn.commit()
    finally:
        conn.close()
    return deleted


async def main():
    logger.info("=== Orphan data cleanup started ===")
    logger.info("Qdrant path: %s", settings.qdrant_path)
    logger.info("Qdrant collection: %s", settings.qdrant_active_collection or settings.qdrant_collection)
    logger.info("Database URL: %s", settings.database_url)

    # 1. Get valid document IDs from SQLite
    valid_ids = _get_all_doc_ids_from_sqlite()
    logger.info("SQLite documents table: %d valid documents", len(valid_ids))

    # 2. Get all document IDs from Qdrant
    vdb = QdrantVectorDB()
    qdrant_ids = _get_all_doc_ids_from_qdrant(vdb)
    qdrant_orphans = qdrant_ids - valid_ids
    logger.info("Qdrant orphans: %d", len(qdrant_orphans))

    # 3. Get all document IDs from BM25
    bm25_ids = _get_all_doc_ids_from_bm25()
    bm25_orphans = bm25_ids - valid_ids
    logger.info("BM25 orphans: %d", len(bm25_orphans))

    if not qdrant_orphans and not bm25_orphans:
        logger.info("No orphan data found — nothing to clean up.")
        return

    # 4. Clean up Qdrant orphans
    qdrant_deleted = 0
    if qdrant_orphans:
        logger.info("Cleaning up %d orphan doc_ids from Qdrant...", len(qdrant_orphans))
        qdrant_deleted = await cleanup_qdrant(vdb, qdrant_orphans)

    # 5. Clean up BM25 orphans
    bm25_deleted = 0
    if bm25_orphans:
        logger.info("Cleaning up %d orphan doc_ids from BM25...", len(bm25_orphans))
        bm25_deleted = cleanup_bm25(bm25_orphans)

    logger.info("=== Cleanup complete: Qdrant chunks=%d, BM25 entries=%d ===",
                qdrant_deleted, bm25_deleted)


if __name__ == "__main__":
    asyncio.run(main())
