import hashlib
from io import BytesIO
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from fastapi import HTTPException, UploadFile
from sqlalchemy import select

from api.documents import (
    _stream_upload_to_staging,
    clear_all_documents,
    delete_document,
    upload_config,
    upload_document_batch,
)
from config import DOCUMENT_UPLOAD_HARD_LIMIT_MB, Settings, settings
from models.database import session_scope
from models.orm import DocStatus, Document
from rag.pipeline import ingest_document_from_path
from storage import get_storage, reset_storage


@pytest.mark.asyncio
async def test_stream_upload_writes_and_hashes_incrementally(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "upload_dir", str(tmp_path))
    reset_storage()
    payload = b"a" * (1024 * 1024 + 37)
    upload = UploadFile(file=BytesIO(payload), filename="large.txt")
    original_read = upload.read
    requested_sizes: list[int] = []

    async def recording_read(size: int = -1) -> bytes:
        requested_sizes.append(size)
        return await original_read(size)

    upload.read = recording_read

    staged, file_hash, file_size = await _stream_upload_to_staging(upload)

    assert requested_sizes
    assert all(size == 1024 * 1024 for size in requested_sizes)
    assert Path(staged.temp_path).read_bytes() == payload
    assert file_hash == hashlib.sha256(payload).hexdigest()
    assert file_size == len(payload)


@pytest.mark.asyncio
async def test_stream_upload_removes_temp_file_when_size_limit_exceeded(
    tmp_path, monkeypatch,
):
    monkeypatch.setattr(settings, "upload_dir", str(tmp_path))
    reset_storage()
    monkeypatch.setattr(settings, "document_max_upload_mb", 1)
    upload = UploadFile(
        file=BytesIO(b"x" * (1024 * 1024 + 1)),
        filename="too-large.txt",
    )

    with pytest.raises(HTTPException) as exc_info:
        await _stream_upload_to_staging(upload)

    assert exc_info.value.status_code == 413
    assert list((tmp_path / ".staging").iterdir()) == []


@pytest.mark.asyncio
async def test_upload_config_exposes_validated_runtime_limit(monkeypatch):
    monkeypatch.setattr(settings, "document_max_upload_mb", 256)
    monkeypatch.setattr(settings, "document_batch_max_files", 40)
    monkeypatch.setattr(settings, "document_batch_max_total_mb", 900)

    result = await upload_config()

    assert result["max_upload_mb"] == 256
    assert result["hard_limit_mb"] == DOCUMENT_UPLOAD_HARD_LIMIT_MB
    assert result["batch_max_files"] == 40
    assert result["batch_max_total_mb"] == 900
    assert ".pdf" in result["allowed_extensions"]


@pytest.mark.asyncio
async def test_batch_upload_isolates_unsupported_file_failures():
    request = MagicMock()
    files = [
        UploadFile(file=BytesIO(b"bad"), filename="bad.exe"),
        UploadFile(file=BytesIO(b"bad2"), filename="bad.zip"),
    ]

    result = await upload_document_batch.__wrapped__(request, files, MagicMock())

    assert result["total"] == 2
    assert result["succeeded"] == 0
    assert result["failed"] == 2
    assert [item["filename"] for item in result["items"]] == ["bad.exe", "bad.zip"]


@pytest.mark.asyncio
async def test_batch_upload_isolates_unexpected_file_failure(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "upload_dir", str(tmp_path))
    reset_storage()
    monkeypatch.setattr(
        "api.documents.ingest_document_from_staged",
        MagicMock(side_effect=RuntimeError("sensitive internal detail")),
    )
    files = [UploadFile(file=BytesIO(b"valid"), filename="valid.txt")]

    result = await upload_document_batch.__wrapped__(MagicMock(), files, MagicMock())

    assert result["failed"] == 1
    assert result["items"][0]["status_code"] == 500
    assert result["items"][0]["error"] == "Internal processing error"
    assert list((tmp_path / ".staging").iterdir()) == []


def test_document_upload_limit_rejects_values_above_hard_cap():
    with pytest.raises(ValueError):
        Settings(
            document_max_upload_mb=DOCUMENT_UPLOAD_HARD_LIMIT_MB + 1,
            _env_file=None,
        )


@pytest.mark.asyncio
async def test_clear_all_rejects_documents_that_are_still_processing():
    async with session_scope() as session:
        session.add(Document(
            id="active-clear-doc",
            filename="active.txt",
            file_hash="active-clear-hash",
            file_size=4,
            file_type=".txt",
            status=DocStatus.embedding,
        ))
        await session.commit()

    async with session_scope() as session:
        with pytest.raises(HTTPException) as exc_info:
            await clear_all_documents(session)

    assert exc_info.value.status_code == 409


@pytest.mark.asyncio
async def test_delete_rejects_document_that_is_still_processing():
    async with session_scope() as session:
        session.add(Document(
            id="active-delete-doc",
            filename="active-delete.txt",
            file_hash="active-delete-hash",
            file_size=4,
            file_type=".txt",
            status=DocStatus.parsing,
        ))
        await session.commit()

    async with session_scope() as session:
        with pytest.raises(HTTPException) as exc_info:
            await delete_document("active-delete-doc", session)

    assert exc_info.value.status_code == 409


@pytest.mark.asyncio
async def test_ingest_from_path_atomically_finalizes_and_registers(
    tmp_path, monkeypatch,
):
    monkeypatch.setattr("storage.files.UPLOAD_DIR", tmp_path)
    monkeypatch.setattr(settings, "upload_dir", str(tmp_path))
    reset_storage()
    payload = b"streamed content"
    temp_path = tmp_path / ".legacy-upload.tmp"
    temp_path.write_bytes(payload)
    manager = MagicMock()

    with patch("worker.tasks.get_task_manager", return_value=manager):
        doc_id = await ingest_document_from_path(
            "streamed.txt",
            str(temp_path),
            hashlib.sha256(payload).hexdigest(),
            len(payload),
            ".txt",
            background=True,
        )

    assert not temp_path.exists()
    manager.create.assert_called_once()
    async with session_scope() as session:
        doc = (
            await session.execute(select(Document).where(Document.id == doc_id))
        ).scalar_one()
        assert doc.status == DocStatus.uploaded
        assert doc.file_size == len(payload)
        assert doc.storage_key is not None
        chunks = [chunk async for chunk in get_storage().open_read(doc.storage_key)]
        assert b"".join(chunks) == payload
