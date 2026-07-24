from contextlib import asynccontextmanager
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import select

from models.database import session_scope
from models.orm import DocStatus, Document
from rag.loaders import OCRModelNotReadyError


async def _add_document(*, status: DocStatus, storage_key: str = "stored/image.png") -> str:
    doc_id = f"ocr-{status.value}"
    async with session_scope() as session:
        session.add(Document(
            id=doc_id,
            filename="scan.png",
            file_hash=f"hash-{status.value}",
            file_size=100,
            file_type=".png",
            storage_key=storage_key,
            status=status,
        ))
        await session.commit()
    return doc_id


@pytest.mark.asyncio
async def test_ingestion_enters_waiting_state_without_failing(monkeypatch):
    import rag.pipeline as pipeline

    doc_id = await _add_document(status=DocStatus.uploaded)

    @asynccontextmanager
    async def fake_materialize(*args, **kwargs):
        yield "scan.png"

    monkeypatch.setattr(pipeline, "materialize", fake_materialize)
    monkeypatch.setattr(
        pipeline,
        "_process_document",
        AsyncMock(side_effect=OCRModelNotReadyError("still downloading")),
    )

    result = await pipeline._run_document_ingestion(
        doc_id, "scan.png", "stored/image.png", ".png", background=False
    )

    assert result == doc_id
    async with session_scope() as session:
        doc = (
            await session.execute(select(Document).where(Document.id == doc_id))
        ).scalar_one()
        assert doc.status == DocStatus.waiting_for_ocr
        assert "still downloading" in (doc.error_message or "")


@pytest.mark.asyncio
async def test_ready_ocr_atomically_resumes_waiting_documents(monkeypatch):
    import rag.pipeline as pipeline

    doc_id = await _add_document(status=DocStatus.waiting_for_ocr)
    storage = AsyncMock()
    storage.exists.return_value = True
    schedule = AsyncMock(return_value=doc_id)
    monkeypatch.setattr(pipeline, "get_storage", lambda: storage)
    monkeypatch.setattr(pipeline, "_run_document_ingestion", schedule)

    resumed = await pipeline.resume_waiting_for_ocr_documents()

    assert resumed == 1
    schedule.assert_awaited_once_with(
        doc_id, "scan.png", "stored/image.png", ".png", background=True
    )
    async with session_scope() as session:
        doc = (
            await session.execute(select(Document).where(Document.id == doc_id))
        ).scalar_one()
        assert doc.status == DocStatus.uploaded
        assert doc.error_message is None


def test_image_loader_reports_model_loading_instead_of_empty_text(tmp_path, monkeypatch):
    pytest.importorskip("cv2")

    import ocr.factory as factory
    from rag.loaders import load_image

    image = tmp_path / "waiting.png"
    image.write_bytes(b"not-decoded-before-readiness-check")
    monkeypatch.setattr(factory.settings, "ocr_enabled", True)
    monkeypatch.setattr(factory, "create_ocr", lambda: object())
    monkeypatch.setattr(
        factory,
        "get_ocr_status",
        lambda: {"status": "downloading", "last_error": ""},
    )
    monkeypatch.setattr(factory, "is_ocr_ready", lambda: False)

    with pytest.raises(OCRModelNotReadyError, match="自动继续"):
        load_image(str(image))
