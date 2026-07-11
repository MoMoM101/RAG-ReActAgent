"""Tests for admin token authentication across all API routes."""

import pytest
from httpx import ASGITransport, AsyncClient

from main import app

TEST_TOKEN = "test-admin-token-12345"


@pytest.fixture
def require_token(monkeypatch):
    """Set a test admin token so require_admin actually enforces auth."""
    from config import settings
    monkeypatch.setattr(settings, "admin_api_token", TEST_TOKEN)


@pytest.fixture
def no_token():
    """Ensure admin token is empty (backwards-compatible, no auth)."""
    from config import settings
    old = settings.admin_api_token
    settings.admin_api_token = ""
    yield
    settings.admin_api_token = old


class TestHealthPublic:
    """Health endpoints must always be public."""

    async def test_health_no_token(self, require_token):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            r = await client.get("/api/health")
            assert r.status_code == 200

    async def test_health_dependencies_no_token(self, require_token):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            r = await client.get("/api/health/dependencies")
            assert r.status_code == 200

    async def test_health_tasks_no_token(self, require_token):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            r = await client.get("/api/health/tasks")
            assert r.status_code == 200


class TestProtectedRoutesNoToken:
    """Business routes return 401 without a token when auth is enabled."""

    async def test_documents_no_token(self, require_token):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            r = await client.get("/api/documents")
            assert r.status_code == 401

    async def test_conversations_no_token(self, require_token):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            r = await client.get("/api/conversations")
            assert r.status_code == 401

    async def test_chat_no_token(self, require_token):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            r = await client.post("/api/chat", json={"message": "hello"})
            assert r.status_code == 401

    async def test_settings_no_token(self, require_token):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            r = await client.get("/api/settings")
            assert r.status_code == 401

    async def test_memories_no_token(self, require_token):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            r = await client.get("/api/memories")
            assert r.status_code == 401

    async def test_metrics_no_token(self, require_token):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            r = await client.get("/api/metrics")
            assert r.status_code == 401


class TestWrongToken:
    """Wrong token returns 401 on protected routes."""

    async def test_wrong_token_documents(self, require_token):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            r = await client.get("/api/documents", headers={"X-Admin-Token": "wrong-token"})
            assert r.status_code == 401

    async def test_wrong_token_chat(self, require_token):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            r = await client.post(
                "/api/chat",
                json={"message": "hello"},
                headers={"X-Admin-Token": "wrong-token"},
            )
            assert r.status_code == 401


class TestCorrectToken:
    """Correct token allows normal business status codes."""

    async def test_documents_with_token(self, require_token):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            r = await client.get("/api/documents", headers={"X-Admin-Token": TEST_TOKEN})
            assert r.status_code == 200

    async def test_conversations_with_token(self, require_token):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            r = await client.get("/api/conversations", headers={"X-Admin-Token": TEST_TOKEN})
            assert r.status_code == 200

    async def test_metrics_with_token(self, require_token):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            r = await client.get("/api/metrics", headers={"X-Admin-Token": TEST_TOKEN})
            assert r.status_code == 200


class TestNoTokenBackwardCompat:
    """When admin token is empty, all routes are public (backwards-compatible)."""

    async def test_documents_public_when_empty(self, no_token):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            r = await client.get("/api/documents")
            assert r.status_code == 200

    async def test_chat_public_when_empty(self, no_token, make_fake_llm):
        from llm.base import LLMResponse
        make_fake_llm([[LLMResponse(content="Hello!")]])
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            r = await client.post("/api/chat", json={"message": "hello"})
            assert r.status_code == 200
