"""Secure first-user bootstrap regression tests."""

import pytest
from sqlalchemy import func, select

from models.database import session_scope
from models.orm import User


async def test_bootstrap_auto_generates_password_when_empty(setup_db, monkeypatch):
    """When BOOTSTRAP_ADMIN_PASSWORD is empty, a random password is generated."""
    from config import settings
    from main import _bootstrap_user

    monkeypatch.setattr(settings, "bootstrap_admin_username", "admin")
    monkeypatch.setattr(settings, "bootstrap_admin_password", "")

    # Should NOT raise — auto-generates password instead
    await _bootstrap_user()

    # Verify user was created with a generated password
    async with session_scope() as session:
        user = await session.scalar(select(User))
        assert user is not None
        assert user.username == "admin"
        assert str(user.role) == "system_admin"


async def test_bootstrap_rejects_weak_or_oversized_password(setup_db, monkeypatch):
    from config import settings
    from main import _bootstrap_user

    monkeypatch.setattr(settings, "bootstrap_admin_username", "admin")
    for password in ("too-short", "密" * 30):
        monkeypatch.setattr(settings, "bootstrap_admin_password", password)
        with pytest.raises(RuntimeError, match="12-72 byte"):
            await _bootstrap_user()


async def test_bootstrap_creates_configured_admin_once(setup_db, monkeypatch):
    from auth.jwt import verify_password

    from config import settings
    from main import _bootstrap_user

    monkeypatch.setattr(settings, "bootstrap_admin_username", "release-admin")
    monkeypatch.setattr(settings, "bootstrap_admin_password", "a-strong-bootstrap-password")

    await _bootstrap_user()
    await _bootstrap_user()

    async with session_scope() as session:
        assert await session.scalar(select(func.count()).select_from(User)) == 1
        user = await session.scalar(select(User))
        assert user is not None
        assert user.username == "release-admin"
        assert str(user.role) == "system_admin"
        assert verify_password("a-strong-bootstrap-password", user.password_hash)


async def test_existing_user_does_not_require_bootstrap_secret(
    bootstrap_admin,
    monkeypatch,
):
    from config import settings
    from main import _bootstrap_user

    monkeypatch.setattr(settings, "bootstrap_admin_password", "")
    await _bootstrap_user()
