"""Shared JWT login helper for benchmark commands."""

import httpx


async def login_access_token(base_url: str, username: str, password: str) -> str:
    async with httpx.AsyncClient(base_url=base_url, timeout=15.0) as client:
        response = await client.post(
            "/api/auth/login",
            json={"username": username, "password": password},
        )
        response.raise_for_status()
        token = response.json().get("access_token")
        if not token:
            raise RuntimeError("Login response did not contain an access token")
        return str(token)
