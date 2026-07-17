"""Read-only live index consistency test.

Validates that BM25 and Qdrant chunk-ID sets match for every ready document
in the running E2E stack. Does NOT create, delete, or rebuild any collection
or index. Does NOT use the database-reset fixture.

Usage:
  DOCKER_E2E_REQUIRED=1 BACKEND_URL=http://127.0.0.1:18000 \
    pytest tests/e2e/test_live_index_consistency.py -v
"""

import os

import pytest
from sqlalchemy import select

from models.database import async_session
from models.orm import Document, DocStatus, GenerationStatus, IndexGeneration
from textdb.bm25_search import BM25Search
from vectordb.factory import create_vectordb

IS_STRICT = os.environ.get("DOCKER_E2E_REQUIRED", "") == "1"


def _require(condition: bool, message: str) -> None:
    if condition:
        return
    if IS_STRICT:
        pytest.fail(message)
    else:
        pytest.skip(message)


async def _get_ready_documents() -> list[dict]:
    """Return all ready documents with active_generation_id."""
    async with async_session() as session:
        result = await session.execute(
            select(Document).where(Document.status == DocStatus.ready)
        )
        docs = result.scalars().all()
        return [
            {
                "id": d.id,
                "filename": d.filename,
                "file_hash": d.file_hash,
                "chunk_count": d.chunk_count,
                "active_generation_id": d.active_generation_id,
            }
            for d in docs
        ]


async def _get_generation(gen_id: str) -> dict | None:
    """Return generation record or None."""
    async with async_session() as session:
        result = await session.execute(
            select(IndexGeneration).where(IndexGeneration.id == gen_id)
        )
        gen = result.scalar_one_or_none()
        if gen is None:
            return None
        return {
            "id": gen.id,
            "doc_id": gen.doc_id,
            "status": str(gen.status),
            "vector_chunk_count": gen.vector_chunk_count,
            "bm25_count": gen.bm25_count,
        }


async def _get_bm25_chunk_ids(document_id: str) -> set[str]:
    """Get chunk IDs from BM25 for a document."""
    bm25 = BM25Search()
    ids = await bm25.get_chunk_ids_by_document(document_id)
    return set(ids)


async def _get_qdrant_chunk_ids(document_id: str) -> set[str]:
    """Get chunk IDs from the active Qdrant collection for a document."""
    vectordb = await create_vectordb()
    ids = await vectordb.get_chunk_ids_by_document(document_id)
    return set(ids)


@pytest.mark.docker
@pytest.mark.asyncio
class TestLiveIndexConsistency:
    async def test_has_ready_documents(self):
        docs = await _get_ready_documents()
        _require(len(docs) > 0, "No ready documents found in live database")

    async def test_active_generation_points_to_committed(self):
        docs = await _get_ready_documents()
        for doc in docs:
            gen_id = doc["active_generation_id"]
            _require(
                gen_id is not None and gen_id != "",
                f"Document {doc['id']} ({doc['filename']}) has no active_generation_id",
            )
            gen = await _get_generation(gen_id)
            _require(
                gen is not None,
                f"Document {doc['id']}: generation {gen_id} not found",
            )
            _require(
                gen["status"] == GenerationStatus.committed,
                f"Document {doc['id']}: generation {gen_id} status is "
                f"'{gen['status']}', expected 'committed'",
            )

    async def test_bm25_qdrant_chunk_ids_match(self):
        docs = await _get_ready_documents()
        for doc in docs:
            bm25_ids = await _get_bm25_chunk_ids(doc["id"])
            qdrant_ids = await _get_qdrant_chunk_ids(doc["id"])

            _require(
                len(bm25_ids) > 0,
                f"Document {doc['id']} ({doc['filename']}): no BM25 chunk IDs found",
            )
            _require(
                len(qdrant_ids) > 0,
                f"Document {doc['id']} ({doc['filename']}): no Qdrant chunk IDs found",
            )

            bm25_only = bm25_ids - qdrant_ids
            qdrant_only = qdrant_ids - bm25_ids

            _require(
                len(bm25_only) == 0,
                f"Document {doc['id']} ({doc['filename']}): {len(bm25_only)} chunks "
                f"in BM25 but not Qdrant: {sorted(bm25_only)[:5]}",
            )
            _require(
                len(qdrant_only) == 0,
                f"Document {doc['id']} ({doc['filename']}): {len(qdrant_only)} chunks "
                f"in Qdrant but not BM25: {sorted(qdrant_only)[:5]}",
            )

    async def test_chunk_count_matches_expected(self):
        docs = await _get_ready_documents()
        for doc in docs:
            bm25_ids = await _get_bm25_chunk_ids(doc["id"])
            qdrant_ids = await _get_qdrant_chunk_ids(doc["id"])

            expected = doc["chunk_count"]
            _require(
                len(bm25_ids) == expected,
                f"Document {doc['id']} ({doc['filename']}): BM25 has "
                f"{len(bm25_ids)} chunks, document.chunk_count={expected}",
            )
            _require(
                len(qdrant_ids) == expected,
                f"Document {doc['id']} ({doc['filename']}): Qdrant has "
                f"{len(qdrant_ids)} chunks, document.chunk_count={expected}",
            )
