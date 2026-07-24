from unittest.mock import AsyncMock, patch

import pytest

from worker.ingestion import ingest_document_async


@pytest.mark.asyncio
async def test_ingest_document_async_delegates_all_arguments():
    ingest = AsyncMock(return_value="document-id")
    with patch("worker.ingestion.ingest_document", ingest):
        result = await ingest_document_async("guide.txt", b"content", ".txt")

    assert result == "document-id"
    ingest.assert_awaited_once_with("guide.txt", b"content", ".txt")
