"""JWT authentication regression tests for public and protected API routes."""

from pathlib import Path

from httpx import ASGITransport, AsyncClient

from main import app

TEST_USERNAME = "admin"
TEST_PASSWORD = "test-admin-password-123"
FIXTURES_DIR = Path(__file__).resolve().parents[1] / "fixtures"


async def _login(client: AsyncClient) -> dict:
    response = await client.post(
        "/api/auth/login",
        json={"username": TEST_USERNAME, "password": TEST_PASSWORD},
    )
    assert response.status_code == 200, response.text
    return response.json()


class TestHealthPublic:
    async def test_health_endpoints_need_no_token(self):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            for path in ("/api/health", "/api/health/dependencies", "/api/health/tasks"):
                response = await client.get(path)
                assert response.status_code == 200


class TestJwtRequired:
    async def test_business_routes_reject_missing_token(self):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            requests = (
                client.get("/api/documents"),
                client.get("/api/conversations"),
                client.get("/api/settings"),
                client.get("/api/memories"),
                client.get("/api/metrics"),
                client.post("/api/chat", json={"message": "hello"}),
            )
            for request in requests:
                response = await request
                assert response.status_code == 401

    async def test_legacy_admin_header_is_rejected(self):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get(
                "/api/documents",
                headers={"X-Admin-Token": "legacy-token"},
            )
            assert response.status_code == 401

    async def test_malformed_bearer_token_is_rejected(self):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get(
                "/api/documents",
                headers={"Authorization": "Bearer not-a-jwt"},
            )
            assert response.status_code == 401


class TestLoginAndRefresh:
    async def test_login_sets_httponly_refresh_cookie(self, bootstrap_admin):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            data = await _login(client)
            assert data["access_token"]
            assert "refresh_token" not in data
            assert data["user"]["username"] == TEST_USERNAME
            assert data["user"]["role"] == "system_admin"
            cookie = client.cookies.get("rag_refresh_token")
            assert cookie
            set_cookie = next(
                item
                for item in client.cookies.jar
                if item.name == "rag_refresh_token"
            )
            assert set_cookie.has_nonstandard_attr("HttpOnly")
            assert set_cookie.path == "/api/auth"

    async def test_wrong_password_is_generic_401(self, bootstrap_admin):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.post(
                "/api/auth/login",
                json={"username": TEST_USERNAME, "password": "wrong-password"},
            )
            assert response.status_code == 401
            assert response.json()["detail"] == "Invalid credentials"

    async def test_oversized_utf8_password_is_rejected_without_server_error(
        self,
        bootstrap_admin,
    ):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.post(
                "/api/auth/login",
                json={"username": TEST_USERNAME, "password": "密" * 30},
            )
            assert response.status_code == 401
            assert response.json()["detail"] == "Invalid credentials"

    async def test_access_token_reaches_protected_route(self, bootstrap_admin):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            token = (await _login(client))["access_token"]
            response = await client.get(
                "/api/documents",
                headers={"Authorization": f"Bearer {token}"},
            )
            assert response.status_code == 200

    async def test_me_returns_authenticated_identity(self, bootstrap_admin):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            token = (await _login(client))["access_token"]
            response = await client.get(
                "/api/auth/me",
                headers={"Authorization": f"Bearer {token}"},
            )
            assert response.status_code == 200
            assert response.json()["username"] == TEST_USERNAME
            assert response.json()["role"] == "system_admin"

    async def test_refresh_cookie_rotates(self, bootstrap_admin):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            login = await _login(client)
            previous_cookie = client.cookies.get("rag_refresh_token")
            response = await client.post("/api/auth/refresh")
            assert response.status_code == 200
            refreshed = response.json()
            assert refreshed["access_token"] != login["access_token"]
            assert "refresh_token" not in refreshed
            assert client.cookies.get("rag_refresh_token") != previous_cookie

    async def test_refresh_requires_cookie(self, bootstrap_admin):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.post("/api/auth/refresh")
            assert response.status_code == 401

    async def test_logout_clears_refresh_cookie(self, bootstrap_admin):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            await _login(client)
            assert client.cookies.get("rag_refresh_token")
            response = await client.post("/api/auth/logout")
            assert response.status_code == 204
            assert client.cookies.get("rag_refresh_token") is None

    async def test_legacy_bcrypt_password_still_logs_in(self, bootstrap_admin):
        import bcrypt
        from sqlalchemy import select

        from models.database import session_scope
        from models.orm import User

        async with session_scope() as session:
            user = await session.scalar(select(User).where(User.username == TEST_USERNAME))
            assert user is not None
            user.password_hash = bcrypt.hashpw(
                TEST_PASSWORD.encode(),
                bcrypt.gensalt(),
            ).decode()
            await session.commit()

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            login = await _login(client)
            assert login["user"]["username"] == TEST_USERNAME

    async def test_change_password_rotates_credentials(self, bootstrap_admin):
        new_password = "新" * 100
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            login = await _login(client)
            old_refresh_cookie = client.cookies.get("rag_refresh_token")
            assert old_refresh_cookie
            headers = {"Authorization": f"Bearer {login['access_token']}"}

            wrong = await client.post(
                "/api/auth/change-password",
                json={
                    "current_password": "wrong-password",
                    "new_password": "Safer-local-passphrase-2026",
                },
                headers=headers,
            )
            assert wrong.status_code == 400

            changed = await client.post(
                "/api/auth/change-password",
                json={
                    "current_password": TEST_PASSWORD,
                    "new_password": new_password,
                },
                headers=headers,
            )
            assert changed.status_code == 200

            async with AsyncClient(
                transport=transport,
                base_url="http://test",
                cookies={"rag_refresh_token": old_refresh_cookie},
            ) as stale_client:
                old_refresh = await stale_client.post("/api/auth/refresh")
                assert old_refresh.status_code == 401

            old_login = await client.post(
                "/api/auth/login",
                json={"username": TEST_USERNAME, "password": TEST_PASSWORD},
            )
            assert old_login.status_code == 401
            new_login = await client.post(
                "/api/auth/login",
                json={
                    "username": TEST_USERNAME,
                    "password": new_password,
                },
            )
            assert new_login.status_code == 200


class TestProtectedOperations:
    async def test_upload_and_chat_accept_bearer_token(
        self,
        bootstrap_admin,
        make_fake_llm,
    ):
        from llm.base import LLMResponse

        make_fake_llm(
            [
                [LLMResponse(content="Hello!", tool_calls=None)],
                [LLMResponse(content="Hello!")],
            ]
        )
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            token = (await _login(client))["access_token"]
            headers = {"Authorization": f"Bearer {token}"}
            with (FIXTURES_DIR / "sample.txt").open("rb") as source:
                upload = await client.post(
                    "/api/documents/upload",
                    files={"file": ("sample.txt", source, "text/plain")},
                    headers=headers,
                )
            assert upload.status_code == 200

            async with client.stream(
                "POST",
                "/api/chat",
                json={"message": "hello"},
                headers=headers,
            ) as response:
                assert response.status_code == 200
                assert b"Hello!" in await response.aread()
