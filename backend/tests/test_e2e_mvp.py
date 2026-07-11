import os
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient

from main import app

FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures"

needs_embedding = pytest.mark.skipif(
    not (os.environ.get("EMBEDDING_API_KEY") or os.environ.get("LLM_API_KEY"))
    and not (__import__("config").settings.embedding_api_key
             or __import__("config").settings.llm_api_key),
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
async def test_list_documents_empty(admin_headers, enable_admin_token):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/api/documents", headers=admin_headers)
        assert response.status_code == 200
        assert isinstance(response.json(), list)


@pytest.mark.asyncio
async def test_create_and_delete_conversation(admin_headers, enable_admin_token):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post("/api/conversations", json={"title": "E2E Test"}, headers=admin_headers)
        assert response.status_code == 200
        conv_id = response.json()["id"]

        response = await client.get("/api/conversations", headers=admin_headers)
        assert response.status_code == 200
        convs = response.json()
        assert any(c["id"] == conv_id for c in convs)

        response = await client.delete(f"/api/conversations/{conv_id}", headers=admin_headers)
        assert response.status_code == 200


@pytest.mark.asyncio
@needs_embedding
async def test_upload_document(admin_headers, enable_admin_token):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        with (FIXTURES_DIR / "sample.txt").open("rb") as f:
            response = await client.post(
                "/api/documents/upload",
                files={"file": ("sample.txt", f, "text/plain")},
                headers=admin_headers,
            )
        assert response.status_code == 200
        doc = response.json()
        assert doc["status"] in ("uploaded", "ready", "parsing", "chunking", "embedding", "indexing")
        assert doc["filename"] == "sample.txt"


@pytest.mark.asyncio
@needs_embedding
async def test_upload_duplicate(admin_headers, enable_admin_token):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        with (FIXTURES_DIR / "sample.txt").open("rb") as f:
            await client.post(
                "/api/documents/upload",
                files={"file": ("sample.txt", f, "text/plain")},
                headers=admin_headers,
            )
        with (FIXTURES_DIR / "sample.txt").open("rb") as f:
            response = await client.post(
                "/api/documents/upload",
                files={"file": ("sample_dup.txt", f, "text/plain")},
                headers=admin_headers,
            )
        assert response.status_code == 409


@pytest.mark.asyncio
async def test_invalid_file_type(admin_headers, enable_admin_token):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/api/documents/upload",
            files={"file": ("test.exe", b"malware", "application/octet-stream")},
            headers=admin_headers,
        )
        assert response.status_code == 400
