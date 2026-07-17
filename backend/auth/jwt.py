"""JWT creation and verification helpers."""

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import jwt as _jwt

from config import settings


def _get_secret() -> str:
    secret = settings.jwt_secret
    if not secret:
        import secrets
        secret = secrets.token_urlsafe(32)
        settings.jwt_secret = secret
    return secret


def create_access_token(user_id: str, username: str, role: str) -> str:
    now = datetime.now(UTC)
    payload = {
        "sub": user_id,
        "username": username,
        "role": role,
        "type": "access",
        "iat": now,
        "exp": now + timedelta(minutes=settings.jwt_access_token_expire_minutes),
        "jti": uuid.uuid4().hex,
    }
    return _jwt.encode(payload, _get_secret(), algorithm="HS256")


def create_refresh_token(user_id: str) -> str:
    now = datetime.now(UTC)
    payload = {
        "sub": user_id,
        "type": "refresh",
        "iat": now,
        "exp": now + timedelta(days=settings.jwt_refresh_token_expire_days),
        "jti": uuid.uuid4().hex,
    }
    return _jwt.encode(payload, _get_secret(), algorithm="HS256")


def decode_token(token: str) -> dict[str, Any]:
    return _jwt.decode(token, _get_secret(), algorithms=["HS256"])


def hash_password(password: str) -> str:
    import bcrypt
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()


def verify_password(password: str, password_hash: str) -> bool:
    import bcrypt
    return bcrypt.checkpw(password.encode(), password_hash.encode())
