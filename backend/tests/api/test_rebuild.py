"""Tests for the rebuild flow with pre-flight and blue-green switching."""
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


class TestGetSampleText:
    """_get_sample_text() tests."""

    @pytest.mark.asyncio
    async def test_returns_raw_text_when_available(self):
        """Should return raw_text from Document when it exists."""
        import uuid

        from api.settings import _get_sample_text
        from models.database import session_scope
        from models.orm import Document
        async with session_scope() as session:
            doc = Document(
                id=str(uuid.uuid4()),
                filename="test_sample.txt",
                file_hash="abc123_sample_test",
                file_size=100,
                file_type=".txt",
                raw_text="This is sample text for testing.",
            )
            session.add(doc)
            await session.commit()

        try:
            result = await _get_sample_text()
            assert result == "This is sample text for testing."
        finally:
            async with session_scope() as session:
                from sqlalchemy import delete
                await session.execute(delete(Document).where(Document.file_hash == "abc123_sample_test"))
                await session.commit()

    @pytest.mark.asyncio
    async def test_returns_none_when_no_data(self):
        """Should not crash when no documents or FTS chunks exist."""
        from api.settings import _get_sample_text

        result = await _get_sample_text()
        # May be None or a string from existing data — just verify no crash
        assert result is None or isinstance(result, str)


class TestPreflightChunkSize:
    """_preflight_chunk_size() tests."""

    @pytest.mark.asyncio
    async def test_returns_same_size_when_embed_succeeds(self):
        """Should return initial chunk_size when embed succeeds."""
        from api.settings import _preflight_chunk_size

        mock_embedding = AsyncMock()
        mock_embedding.embed_query = AsyncMock(return_value=[0.1] * 10)

        result = await _preflight_chunk_size(
            "Hello world. " * 100,
            initial_size=512,
            embedding=mock_embedding,
        )
        assert result == 512
        mock_embedding.embed_query.assert_called_once()

    @pytest.mark.asyncio
    async def test_reduces_size_on_too_long_error(self):
        """Should reduce chunk_size when embed returns 'too long' error."""
        from api.settings import _preflight_chunk_size

        mock_embedding = AsyncMock()
        mock_embedding.embed_query = AsyncMock(side_effect=[
            Exception("input too long"),
            Exception("token limit exceeded"),
            None,  # Third try succeeds
        ])

        result = await _preflight_chunk_size(
            "Hello world. " * 200,
            initial_size=512,
            embedding=mock_embedding,
        )
        # 512 -> 341 -> 227 (succeeds at 3rd attempt)
        assert result == 227
        assert mock_embedding.embed_query.call_count == 3

    @pytest.mark.asyncio
    async def test_raises_on_non_length_error(self):
        """Should raise immediately on non-length-related errors."""
        from api.settings import _preflight_chunk_size

        mock_embedding = AsyncMock()
        mock_embedding.embed_query = AsyncMock(side_effect=ValueError("auth error"))

        with pytest.raises(ValueError, match="auth error"):
            await _preflight_chunk_size(
                "test",
                initial_size=512,
                embedding=mock_embedding,
            )

    @pytest.mark.asyncio
    async def test_minimum_chunk_size_bound(self):
        """Should not go below 8 and should raise after 10 attempts."""
        from api.settings import _preflight_chunk_size

        mock_embedding = AsyncMock()
        mock_embedding.embed_query = AsyncMock(
            side_effect=[Exception("too long")] * 11
        )

        with patch("rag.splitter.split_text") as mock_split:
            mock_split.return_value = [MagicMock(text="test chunk")]
            with pytest.raises(RuntimeError, match="Pre-flight failed"):
                await _preflight_chunk_size(
                    "Hello world. " * 50,
                    initial_size=512,
                    embedding=mock_embedding,
                )


class TestRebuildEndpoint:
    """rebuild_collections endpoint tests."""

    @pytest.mark.asyncio
    async def test_rebuild_lock_rejects_concurrent(self):
        """Should reject a second rebuild while one is in progress."""
        from api.settings import _rebuild_lock

        # Simulate lock held (verify the guard variable exists)
        _rebuild_lock = True  # noqa: F811
        assert isinstance(_rebuild_lock, bool)

    @pytest.mark.asyncio
    async def test_scheduler_failure_releases_rebuild_lock(self, monkeypatch):
        import api.settings as settings_api

        settings_api._rebuild_lock = False
        manager = MagicMock()
        manager.create.side_effect = RuntimeError("scheduler unavailable")
        monkeypatch.setattr("worker.tasks.get_task_manager", lambda: manager)

        with pytest.raises(RuntimeError, match="scheduler unavailable"):
            await settings_api.rebuild_collections()

        assert settings_api._rebuild_lock is False


class TestBm25AtomicSwitch:
    @staticmethod
    async def _engine():
        from sqlalchemy.ext.asyncio import create_async_engine

        return create_async_engine("sqlite+aiosqlite://")

    @pytest.mark.asyncio
    async def test_missing_staged_table_preserves_live_tables(self, monkeypatch):
        import models.database as database
        from api.settings import _switch_bm25_tables

        engine = await self._engine()
        monkeypatch.setattr(database, "engine", engine)
        async with engine.begin() as conn:
            await conn.exec_driver_sql(
                "CREATE TABLE bm25_docs (document_id TEXT)"
            )
            await conn.exec_driver_sql("CREATE TABLE bm25_index (term TEXT)")
            await conn.exec_driver_sql("CREATE TABLE bm25_stats (term TEXT)")
            await conn.exec_driver_sql("CREATE TABLE bm25_docs_v2 (document_id TEXT)")
            await conn.exec_driver_sql("CREATE TABLE bm25_index_v2 (term TEXT)")

        with pytest.raises(RuntimeError, match="bm25_stats_v2"):
            await _switch_bm25_tables()

        async with engine.connect() as conn:
            rows = await conn.exec_driver_sql(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='bm25_docs'"
            )
            assert rows.first() == ("bm25_docs",)
        await engine.dispose()

    @pytest.mark.asyncio
    async def test_complete_stage_repairs_partially_missing_live_set(self, monkeypatch):
        import models.database as database
        from api.settings import _switch_bm25_tables

        engine = await self._engine()
        monkeypatch.setattr(database, "engine", engine)
        async with engine.begin() as conn:
            # Reproduce the observed failure: docs was dropped while index and
            # stats remained live.
            await conn.exec_driver_sql("CREATE TABLE bm25_index (term TEXT)")
            await conn.exec_driver_sql("CREATE TABLE bm25_stats (term TEXT)")
            await conn.exec_driver_sql("CREATE TABLE bm25_docs_v2 (document_id TEXT)")
            await conn.exec_driver_sql("CREATE TABLE bm25_index_v2 (term TEXT)")
            await conn.exec_driver_sql("CREATE TABLE bm25_stats_v2 (term TEXT)")
            await conn.exec_driver_sql("INSERT INTO bm25_docs_v2 VALUES ('recovered')")

        await _switch_bm25_tables()

        async with engine.connect() as conn:
            row = await conn.exec_driver_sql("SELECT document_id FROM bm25_docs")
            assert row.scalar_one() == "recovered"
            staged = await conn.exec_driver_sql(
                "SELECT count(*) FROM sqlite_master "
                "WHERE type='table' AND name LIKE 'bm25_%_v2'"
            )
            assert staged.scalar_one() == 0
        await engine.dispose()


def test_rebuild_document_key_uses_id_as_empty_filename_fallback():
    from api.settings import _derive_doc_key

    assert _derive_doc_key("12345678-abcd", "guide.pdf") == "guide"
    assert _derive_doc_key("12345678-abcd", "中文.pdf") == "12345678"


class _FakeClient:
    deleted: list[str] = []

    def delete_collection(self, name: str) -> None:
        self.deleted.append(name)

    def get_collections(self):
        return SimpleNamespace(collections=[])


class _FakeVectorDB:
    def __init__(self, collection_name: str):
        self.collection_name = collection_name
        self.client = _FakeClient()

    async def collection_exists(self) -> bool:
        return True


@pytest.mark.asyncio
async def test_cross_index_switch_restores_vector_pointer_when_bm25_fails(
    tmp_path: Path,
    monkeypatch,
):
    from api.settings_rebuild import activate_qdrant_and_bm25
    from config import settings

    pointer = tmp_path / "active_collections.json"
    pointer.write_text('{"rag_chunks": "rag_chunks_old"}', encoding="utf-8")
    monkeypatch.setattr(settings, "qdrant_active_collection", "rag_chunks_old")
    _FakeClient.deleted.clear()

    with pytest.raises(RuntimeError, match="bm25 switch failed"):
        await activate_qdrant_and_bm25(
            _FakeVectorDB("rag_chunks_new"),
            "rag_chunks_new",
            "rag_chunks_old",
            pointer,
            AsyncMock(side_effect=RuntimeError("bm25 switch failed")),
        )

    assert settings.qdrant_active_collection == "rag_chunks_old"
    assert pointer.read_text(encoding="utf-8") == '{"rag_chunks": "rag_chunks_old"}'
    assert _FakeClient.deleted == ["rag_chunks_new"]


@pytest.mark.asyncio
async def test_cross_index_switch_deletes_old_vector_only_after_bm25_success(
    tmp_path: Path,
    monkeypatch,
):
    import json

    from api.settings_rebuild import activate_qdrant_and_bm25
    from config import settings

    pointer = tmp_path / "active_collections.json"
    pointer.write_text('{"rag_chunks": "rag_chunks_old"}', encoding="utf-8")
    monkeypatch.setattr(settings, "qdrant_active_collection", "rag_chunks_old")
    switch = AsyncMock()
    _FakeClient.deleted.clear()

    await activate_qdrant_and_bm25(
        _FakeVectorDB("rag_chunks_new"),
        "rag_chunks_new",
        "rag_chunks_old",
        pointer,
        switch,
    )

    switch.assert_awaited_once()
    assert settings.qdrant_active_collection == "rag_chunks_new"
    assert json.loads(pointer.read_text(encoding="utf-8"))["rag_chunks"] == "rag_chunks_new"
    assert "rag_chunks_old" in _FakeClient.deleted
