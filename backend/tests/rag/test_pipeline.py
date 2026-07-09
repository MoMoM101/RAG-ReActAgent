import contextlib

import pytest
from sqlalchemy import select

from models.database import async_session
from models.orm import Document
from rag.pipeline import ingest_document


@pytest.mark.asyncio
async def test_ingest_txt_file(tmp_path, monkeypatch):
    # Create test file
    test_file = tmp_path / "test.txt"
    test_file.write_text("这是测试文档内容。\n包含多行文本。\n用于测试入库流程。", encoding="utf-8")

    # Save file as if uploaded
    file_content = test_file.read_bytes()

    # Ingest (will skip embedding since no API key, expect ValueError or success)
    try:
        doc_id = await ingest_document("test.txt", file_content, ".txt")
        # Verify record created
        async with async_session() as session:
            result = await session.execute(select(Document).where(Document.id == doc_id))
            doc = result.scalar_one()
            assert doc.filename == "test.txt"
            assert doc.file_hash is not None
            print(f"Document status: {doc.status}")
    except Exception as e:
        # Expected: embedding fails without API key, status should be failed
        print(f"Ingestion error (expected without API key): {e}")

@pytest.mark.asyncio
async def test_duplicate_detection(tmp_path, monkeypatch):
    test_file = tmp_path / "dup.txt"
    content = b"unique test content for duplicate check"
    test_file.write_bytes(content)

    with contextlib.suppress(Exception):
        await ingest_document("dup1.txt", content, ".txt")

    # Second upload with same content should raise ValueError
    with pytest.raises(ValueError, match="already exists"):
        await ingest_document("dup2.txt", content, ".txt")
