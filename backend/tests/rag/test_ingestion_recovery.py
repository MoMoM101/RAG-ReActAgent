from unittest.mock import MagicMock, patch

import pytest
from sqlalchemy import select
from storage import reset_storage

from config import settings
from models.database import session_scope
from models.orm import DocStatus, Document
from rag.pipeline import recover_incomplete_documents


async def _add_document(doc_id: str, filename: str, status: DocStatus) -> None:
    async with session_scope() as session:
        session.add(Document(
            id=doc_id,
            filename=filename,
            file_hash=f"hash-{doc_id}",
            file_size=4,
            file_type=".txt",
            status=status,
            error_message="interrupted",
        ))
        await session.commit()


@pytest.mark.asyncio
async def test_recovery_reschedules_incomplete_document(tmp_path, monkeypatch):
    monkeypatch.setattr("storage.files.UPLOAD_DIR", tmp_path)
    monkeypatch.setattr(settings, "upload_dir", str(tmp_path))
    reset_storage()
    (tmp_path / "recover.txt").write_text("test", encoding="utf-8")
    await _add_document("recover-doc", "recover.txt", DocStatus.embedding)
    manager = MagicMock()

    with patch("worker.tasks.get_task_manager", return_value=manager):
        recovered = await recover_incomplete_documents()

    assert recovered == 1
    manager.create.assert_called_once()
    async with session_scope() as session:
        doc = (
            await session.execute(
                select(Document).where(Document.id == "recover-doc")
            )
        ).scalar_one()
        assert doc.status == DocStatus.uploaded
        assert doc.error_message is None


@pytest.mark.asyncio
async def test_recovery_marks_missing_source_as_failed(tmp_path, monkeypatch):
    monkeypatch.setattr("storage.files.UPLOAD_DIR", tmp_path)
    monkeypatch.setattr(settings, "upload_dir", str(tmp_path))
    reset_storage()
    await _add_document("missing-doc", "missing.txt", DocStatus.uploaded)
    manager = MagicMock()

    with patch("worker.tasks.get_task_manager", return_value=manager):
        recovered = await recover_incomplete_documents()

    assert recovered == 0
    manager.create.assert_not_called()
    async with session_scope() as session:
        doc = (
            await session.execute(
                select(Document).where(Document.id == "missing-doc")
            )
        ).scalar_one()
        assert doc.status == DocStatus.failed
        assert "原始文件不存在" in (doc.error_message or "")
