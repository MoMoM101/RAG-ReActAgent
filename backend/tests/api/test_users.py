"""Users CRUD API tests."""

from httpx import ASGITransport, AsyncClient

from main import app


class TestUsersApi:
    async def _get_token(self, client) -> str:
        r = await client.post("/api/auth/login", json={
            "username": "admin",
            "password": "test-admin-password-123",
        })
        assert r.status_code == 200, f"Login failed: {r.text}"
        return r.json()["access_token"]

    async def test_list_users(self, bootstrap_admin):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            token = await self._get_token(client)
            r = await client.get("/api/users/", headers={
                "Authorization": f"Bearer {token}",
            })
            assert r.status_code == 200
            users = r.json()
            assert len(users) >= 1
            assert any(u["username"] == "admin" for u in users)

    async def test_create_and_delete_user(self, bootstrap_admin):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            token = await self._get_token(client)
            r = await client.post("/api/users/", json={
                "username": "test_editor",
                "password": "test-user-password-123",
                "role": "editor",
            }, headers={"Authorization": f"Bearer {token}"})
            assert r.status_code == 201
            user_id = r.json()["id"]

            r = await client.delete(f"/api/users/{user_id}", headers={
                "Authorization": f"Bearer {token}",
            })
            assert r.status_code == 200
            assert r.json()["status"] == "deleted"

    async def test_cannot_delete_self(self, bootstrap_admin):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            token = await self._get_token(client)
            me = await client.get("/api/auth/me", headers={
                "Authorization": f"Bearer {token}",
            })
            my_id = me.json()["user_id"]
            r = await client.delete(f"/api/users/{my_id}", headers={
                "Authorization": f"Bearer {token}",
            })
            assert r.status_code == 400

    async def test_duplicate_username_rejected(self, bootstrap_admin):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            token = await self._get_token(client)
            r = await client.post("/api/users/", json={
                "username": "admin",
                "password": "whatever-password-123",
                "role": "editor",
            }, headers={"Authorization": f"Bearer {token}"})
            assert r.status_code == 409
