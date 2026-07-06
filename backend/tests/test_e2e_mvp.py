import os

import pytest
from httpx import ASGITransport, AsyncClient

from main import app

needs_embedding = pytest.mark.skipif(
    not os.environ.get("EMBEDDING_API_KEY") and not os.environ.get("LLM_API_KEY"),
    reason="No embedding API key configured",
)


@pytest.mark.asyncio
async def test_health():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/api/health")
        assert response.status_code == 200
        assert response.json()["status"] == "ok"


@pytest.mark.asyncio
async def test_list_documents_empty():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/api/documents")
        assert response.status_code == 200
        assert isinstance(response.json(), list)


@pytest.mark.asyncio
async def test_create_and_delete_conversation():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post("/api/conversations", json={"title": "E2E Test"})
        assert response.status_code == 200
        conv_id = response.json()["id"]

        response = await client.get("/api/conversations")
        assert response.status_code == 200
        convs = response.json()
        assert any(c["id"] == conv_id for c in convs)

        response = await client.delete(f"/api/conversations/{conv_id}")
        assert response.status_code == 200


@pytest.mark.asyncio
@needs_embedding
async def test_upload_document():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        with open("tests/fixtures/sample.txt", "rb") as f:
            response = await client.post(
                "/api/documents/upload",
                files={"file": ("sample.txt", f, "text/plain")},
            )
        assert response.status_code == 200
        doc = response.json()
        assert doc["status"] in ("uploaded", "ready")
        assert doc["filename"] == "sample.txt"


@pytest.mark.asyncio
@needs_embedding
async def test_upload_duplicate():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        with open("tests/fixtures/sample.txt", "rb") as f:
            await client.post(
                "/api/documents/upload",
                files={"file": ("sample.txt", f, "text/plain")},
            )
        with open("tests/fixtures/sample.txt", "rb") as f:
            response = await client.post(
                "/api/documents/upload",
                files={"file": ("sample_dup.txt", f, "text/plain")},
            )
        assert response.status_code == 409


@pytest.mark.asyncio
async def test_invalid_file_type():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/api/documents/upload",
            files={"file": ("test.exe", b"malware", "application/octet-stream")},
        )
        assert response.status_code == 400
