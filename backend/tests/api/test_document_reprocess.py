import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException
from sqlalchemy import select

from api.documents import _document_progress_events, reprocess_document
from config import settings
from models.database import session_scope
from models.orm import DocStatus, Document


async def _create_failed_document(filename: str) -> str:
    doc_id = str(uuid.uuid4())
    async with session_scope() as session:
        session.add(Document(
            id=doc_id,
            filename=filename,
            file_hash=uuid.uuid4().hex,
            file_size=4,
            file_type=".txt",
            status=DocStatus.failed,
            error_message="initial failure",
        ))
        await session.commit()
    return doc_id


@pytest.mark.asyncio
async def test_reprocess_returns_immediately_and_claims_document(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "upload_dir", str(tmp_path))
    filename = "retry.txt"
    (tmp_path / filename).write_text("test", encoding="utf-8")
    doc_id = await _create_failed_document(filename)
    manager = MagicMock()

    async with session_scope() as session:
        with patch("worker.tasks.get_task_manager", return_value=manager):
            result = await reprocess_document(doc_id, session)

    assert result == {"status": "queued", "id": doc_id}
    manager.create.assert_called_once()
    async with session_scope() as session:
        doc = (
            await session.execute(select(Document).where(Document.id == doc_id))
        ).scalar_one()
        assert doc.status == DocStatus.uploaded
        assert doc.error_message is None


@pytest.mark.asyncio
async def test_reprocess_missing_source_keeps_failed_status(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "upload_dir", str(tmp_path))
    doc_id = await _create_failed_document("missing.txt")

    async with session_scope() as session:
        with pytest.raises(HTTPException) as exc_info:
            await reprocess_document(doc_id, session)

    assert exc_info.value.status_code == 404
    async with session_scope() as session:
        doc = (
            await session.execute(select(Document).where(Document.id == doc_id))
        ).scalar_one()
        assert doc.status == DocStatus.failed


@pytest.mark.asyncio
async def test_background_reprocess_failure_persists_and_publishes(
    tmp_path, monkeypatch,
):
    monkeypatch.setattr(settings, "upload_dir", str(tmp_path))
    filename = "retry-fails.txt"
    (tmp_path / filename).write_text("test", encoding="utf-8")
    doc_id = await _create_failed_document(filename)
    manager = MagicMock()

    async with session_scope() as session:
        with (
            patch("worker.tasks.get_task_manager", return_value=manager),
            patch(
                "api.documents._process_document",
                new=AsyncMock(side_effect=RuntimeError("embedding unavailable")),
            ),
            patch("rag.progress.progress.publish") as publish,
        ):
            await reprocess_document(doc_id, session)
            background = manager.create.call_args.args[0]
            await background()

    async with session_scope() as session:
        doc = (
            await session.execute(select(Document).where(Document.id == doc_id))
        ).scalar_one()
        assert doc.status == DocStatus.failed
        assert "embedding unavailable" in (doc.error_message or "")
    assert any(
        call.args[1].get("status") == "failed" for call in publish.call_args_list
    )


@pytest.mark.asyncio
async def test_progress_stream_returns_immediately_for_durable_terminal_state():
    doc_id = await _create_failed_document("terminal.txt")
    events = _document_progress_events(doc_id)

    assert await anext(events) == "retry: 3000\n\n"
    terminal = await anext(events)
    assert '"status": "failed"' in terminal
    assert "initial failure" in terminal
    with pytest.raises(StopAsyncIteration):
        await anext(events)


@pytest.mark.asyncio
async def test_progress_stream_uses_heartbeat_instead_of_timeout(monkeypatch):
    doc_id = str(uuid.uuid4())
    async with session_scope() as session:
        session.add(Document(
            id=doc_id,
            filename="heartbeat.txt",
            file_hash=uuid.uuid4().hex,
            file_size=4,
            file_type=".txt",
            status=DocStatus.embedding,
        ))
        await session.commit()

    async def immediate_timeout(awaitable, *_args, **_kwargs):
        awaitable.close()
        raise TimeoutError

    monkeypatch.setattr("api.documents.asyncio.wait_for", immediate_timeout)
    events = _document_progress_events(doc_id)

    assert await anext(events) == "retry: 3000\n\n"
    assert '"status": "embedding"' in await anext(events)
    assert await anext(events) == ": keepalive\n\n"
    await events.aclose()
