"""Tests for admin token authentication across all API routes."""

from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient

from main import app

TEST_TOKEN = "test-admin-token-12345"
FIXTURES_DIR = Path(__file__).resolve().parents[1] / "fixtures"


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

    async def test_upload_no_token(self, require_token):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            r = await client.post(
                "/api/documents/upload",
                files={"file": ("test.txt", b"content", "text/plain")},
            )
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

    async def test_wrong_token_upload(self, require_token):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            r = await client.post(
                "/api/documents/upload",
                files={"file": ("test.txt", b"content", "text/plain")},
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

    async def test_upload_with_token(self, require_token):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            with (FIXTURES_DIR / "sample.txt").open("rb") as f:
                r = await client.post(
                    "/api/documents/upload",
                    files={"file": ("sample.txt", f, "text/plain")},
                    headers={"X-Admin-Token": TEST_TOKEN},
                )
            assert r.status_code == 200

    async def test_chat_sse_with_token(self, require_token, make_fake_llm):
        from llm.base import LLMResponse
        # Agent loop uses two LLM calls: initial + final answer
        make_fake_llm([
            [LLMResponse(content="Hello!", tool_calls=None)],
            [LLMResponse(content="Hello!")],
        ])
        transport = ASGITransport(app=app)
        async with (
            AsyncClient(transport=transport, base_url="http://test") as client,
            client.stream(
                "POST",
                "/api/chat",
                json={"message": "hello"},
                headers={"X-Admin-Token": TEST_TOKEN},
            ) as r,
        ):
            assert r.status_code == 200
            body = await r.aread()
            assert b"Hello!" in body


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

    async def test_upload_public_when_empty(self, no_token):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            r = await client.post(
                "/api/documents/upload",
                files={"file": ("compat_test_unique.txt", b"unique compat test content", "text/plain")},
            )
            assert r.status_code == 200


class TestJwtAuth:
    """JWT-based authentication tests."""

    @pytest.fixture
    def jwt_mode(self, monkeypatch):
        """Disable legacy token, enable JWT-only mode."""
        from config import settings
        monkeypatch.setattr(settings, "legacy_admin_token_enabled", False)
        monkeypatch.setattr(settings, "admin_api_token", "")

    async def _get_token(self, client) -> str:
        r = await client.post("/api/auth/login", json={
            "username": "admin",
            "password": "admin123",
        })
        assert r.status_code == 200, f"Login failed: {r.text}"
        return r.json()["access_token"]

    async def test_login_returns_tokens(self, jwt_mode, setup_db):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            r = await client.post("/api/auth/login", json={
                "username": "admin",
                "password": "admin123",
            })
            assert r.status_code == 200
            data = r.json()
            assert "access_token" in data
            assert "refresh_token" in data
            assert data["user"]["role"] == "system_admin"

    async def test_login_wrong_password(self, jwt_mode, setup_db):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            r = await client.post("/api/auth/login", json={
                "username": "admin",
                "password": "wrong",
            })
            assert r.status_code == 401

    async def test_no_access_without_token(self, jwt_mode, setup_db):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            r = await client.get("/api/documents")
            assert r.status_code == 401

    async def test_access_with_valid_jwt(self, jwt_mode, setup_db):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            token = await self._get_token(client)
            r = await client.get("/api/documents", headers={
                "Authorization": f"Bearer {token}",
            })
            assert r.status_code == 200

    async def test_me_endpoint(self, jwt_mode, setup_db):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            token = await self._get_token(client)
            r = await client.get("/api/auth/me", headers={
                "Authorization": f"Bearer {token}",
            })
            assert r.status_code == 200
            data = r.json()
            assert data["username"] == "admin"
            assert data["role"] == "system_admin"

    async def test_refresh_token(self, jwt_mode, setup_db):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            r = await client.post("/api/auth/login", json={
                "username": "admin",
                "password": "admin123",
            })
            refresh = r.json()["refresh_token"]
            r = await client.post("/api/auth/refresh", json={
                "refresh_token": refresh,
            })
            assert r.status_code == 200
            assert "access_token" in r.json()
