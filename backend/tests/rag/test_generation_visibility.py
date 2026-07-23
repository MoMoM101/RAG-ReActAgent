"""Test generation atomic visibility: retrieval must only see committed generations."""

import hashlib
import uuid

import pytest
from sqlalchemy import text as sa_text

from models.database import session_scope
from models.orm import Document


def _hash_chunk_ids(chunk_ids: set[str]) -> str:
    return hashlib.sha256(
        "|".join(sorted(chunk_ids)).encode()
    ).hexdigest()


@pytest.fixture(autouse=True)
async def _cleanup():
    yield
    async with session_scope() as session:
        conn = await session.connection()
        await conn.execute(sa_text("DELETE FROM index_generations"))
        await conn.execute(sa_text("DELETE FROM documents"))
        await session.commit()


class TestGenerationStates:
    """Verify generation state transitions are correct."""

    async def test_generation_starts_in_preparing(self):
        """New generation must start in PREPARING state."""
        from rag.pipeline import _create_generation

        gen_id = str(uuid.uuid4())
        doc_id = str(uuid.uuid4())

        async with session_scope() as session:
            doc = Document(id=doc_id, filename="test.txt", file_hash="abc", file_size=100, file_type=".txt")
            session.add(doc)
            await session.commit()

        await _create_generation(gen_id, doc_id)

        async with session_scope() as session:
            conn = await session.connection()
            row = (await conn.execute(
                sa_text("SELECT status FROM index_generations WHERE id=:id"), {"id": gen_id}
            )).fetchone()
        assert row is not None
        assert row[0] == "preparing"

    async def test_generation_committed_with_verified_counts(self):
        """Committed generation must record verified counts and hash."""
        from rag.pipeline import _commit_generation

        gen_id = str(uuid.uuid4())
        doc_id = str(uuid.uuid4())
        chunk_ids = {str(uuid.uuid4()) for _ in range(5)}
        expected_hash = _hash_chunk_ids(chunk_ids)

        async with session_scope() as session:
            doc = Document(id=doc_id, filename="test.txt", file_hash="def", file_size=200, file_type=".txt")
            session.add(doc)
            await session.commit()

        from rag.pipeline import _create_generation
        await _create_generation(gen_id, doc_id)
        await _commit_generation(gen_id, 5, 5, expected_hash)

        async with session_scope() as session:
            conn = await session.connection()
            row = (await conn.execute(
                sa_text("SELECT status, vector_chunk_count, bm25_count, chunk_ids_hash FROM index_generations WHERE id=:id"),
                {"id": gen_id},
            )).fetchone()
        assert row[0] == "committed"
        assert row[1] == 5
        assert row[2] == 5
        assert row[3] == expected_hash

    async def test_generation_failed_with_error_info(self):
        """Failed generation must record stage and message."""
        from rag.pipeline import _fail_generation

        gen_id = str(uuid.uuid4())
        doc_id = str(uuid.uuid4())

        async with session_scope() as session:
            doc = Document(id=doc_id, filename="test.txt", file_hash="ghi", file_size=300, file_type=".txt")
            session.add(doc)
            await session.commit()

        from rag.pipeline import _create_generation
        await _create_generation(gen_id, doc_id)
        await _fail_generation(gen_id, 3, 0, error_stage="writing_bm25", error_message="BM25 insert timeout")

        async with session_scope() as session:
            conn = await session.connection()
            row = (await conn.execute(
                sa_text(
                    "SELECT status, vector_chunk_count, bm25_count, error_stage, error_message "
                    "FROM index_generations WHERE id=:id"
                ),
                {"id": gen_id},
            )).fetchone()
        assert row[0] == "failed"
        assert row[1] == 3
        assert row[2] == 0
        assert row[3] == "writing_bm25"
        assert "timeout" in row[4]


class TestChunkIdVerification:
    """Verify chunk_id sets are compared between Qdrant and BM25."""

    async def test_mismatched_chunk_ids_prevent_commit(self):
        """When Qdrant and BM25 chunk_ids differ, generation must not commit."""
        from rag.pipeline import _verify_generation

        gen_id = str(uuid.uuid4())
        qdrant_ids = {str(uuid.uuid4()) for _ in range(5)}
        bm25_ids = {str(uuid.uuid4()) for _ in range(4)}

        result = await _verify_generation(gen_id, qdrant_ids, bm25_ids)
        assert result is False

    async def test_matching_chunk_ids_pass_verification(self):
        """Matching chunk_id sets must pass verification."""
        from rag.pipeline import _verify_generation

        gen_id = str(uuid.uuid4())
        ids = {str(uuid.uuid4()) for _ in range(5)}

        result = await _verify_generation(gen_id, ids, ids)
        assert result is True


class TestCleanupStaging:
    """Verify cleanup of stale staging generations on startup."""

    async def test_cleanup_staging_on_startup(self):
        """Staging/preparing generations at startup must be cleaned up."""
        from rag.pipeline import cleanup_staging_generations

        gen_id = str(uuid.uuid4())
        doc_id = str(uuid.uuid4())

        async with session_scope() as session:
            doc = Document(id=doc_id, filename="t.txt", file_hash="jkl", file_size=10, file_type=".txt")
            session.add(doc)
            await session.commit()
            conn = await session.connection()
            await conn.execute(
                sa_text("INSERT INTO index_generations (id, doc_id, status, created_at) "
                        "VALUES (:id, :did, 'preparing', datetime('now'))"),
                {"id": gen_id, "did": doc_id},
            )
            await session.commit()

        cleaned = await cleanup_staging_generations()
        assert cleaned >= 1

        async with session_scope() as session:
            conn = await session.connection()
            row = (await conn.execute(
                sa_text("SELECT status FROM index_generations WHERE id=:id"), {"id": gen_id}
            )).fetchone()
        assert row[0] == "failed"

    async def test_cleanup_does_not_delete_newer_active_generation(self, monkeypatch):
        """A stale attempt must not delete indexes owned by a newer commit."""
        from unittest.mock import AsyncMock

        from rag.pipeline import cleanup_staging_generations
        from textdb.bm25_search import BM25Search

        doc_id = str(uuid.uuid4())
        active_gen_id = str(uuid.uuid4())
        stale_gen_id = str(uuid.uuid4())
        async with session_scope() as session:
            doc = Document(
                id=doc_id,
                filename="active.txt",
                file_hash=str(uuid.uuid4()),
                file_size=10,
                file_type=".txt",
                active_generation_id=active_gen_id,
            )
            session.add(doc)
            await session.commit()
            conn = await session.connection()
            await conn.execute(sa_text(
                "INSERT INTO index_generations (id, doc_id, status, created_at) "
                "VALUES (:active, :did, 'committed', datetime('now')), "
                "(:stale, :did, 'preparing', datetime('now'))"
            ), {"active": active_gen_id, "stale": stale_gen_id, "did": doc_id})
            await session.commit()

        vector_delete = AsyncMock()
        bm25_delete = AsyncMock()
        fake_vdb = type("FakeVDB", (), {"delete_by_document": vector_delete})()

        async def fake_create_vectordb():
            return fake_vdb

        monkeypatch.setattr("vectordb.factory.create_vectordb", fake_create_vectordb)
        monkeypatch.setattr(BM25Search, "delete_by_document", bm25_delete)

        await cleanup_staging_generations()

        vector_delete.assert_not_awaited()
        bm25_delete.assert_not_awaited()
        async with session_scope() as session:
            conn = await session.connection()
            status = (await conn.execute(
                sa_text("SELECT status FROM index_generations WHERE id=:id"),
                {"id": stale_gen_id},
            )).scalar_one()
        assert status == "failed"
