"""JWT creation and verification helpers."""

import hashlib
import hmac
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import jwt as _jwt

from config import settings


def _get_secret() -> str:
    secret = settings.jwt_secret.strip()
    if len(secret) < 32:
        raise RuntimeError(
            "JWT_SECRET must be configured with at least 32 characters"
        )
    return secret


def validate_jwt_configuration() -> None:
    """Fail fast when tokens would be signed with an unstable or weak secret."""
    _get_secret()


def create_access_token(
    user_id: str,
    username: str,
    role: str,
) -> str:
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


def credential_version(password_hash: str) -> str:
    """Return an opaque version bound to the current password hash."""
    return hmac.new(
        _get_secret().encode("utf-8"),
        password_hash.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()


def create_refresh_token(user_id: str, password_hash: str) -> str:
    now = datetime.now(UTC)
    payload = {
        "sub": user_id,
        "type": "refresh",
        "credential_version": credential_version(password_hash),
        "iat": now,
        "exp": now + timedelta(days=settings.jwt_refresh_token_expire_days),
        "jti": uuid.uuid4().hex,
    }
    return _jwt.encode(payload, _get_secret(), algorithm="HS256")


def decode_token(token: str) -> dict[str, Any]:
    return _jwt.decode(token, _get_secret(), algorithms=["HS256"])


def hash_password(password: str) -> str:
    import bcrypt

    digest = hashlib.sha256(password.encode("utf-8")).digest()
    return "$bcrypt-sha256$" + bcrypt.hashpw(digest, bcrypt.gensalt()).decode()


def verify_password(password: str, password_hash: str) -> bool:
    import bcrypt

    try:
        if password_hash.startswith("$bcrypt-sha256$"):
            digest = hashlib.sha256(password.encode("utf-8")).digest()
            stored_hash = password_hash.removeprefix("$bcrypt-sha256$")
            return bcrypt.checkpw(digest, stored_hash.encode())

        encoded = password.encode("utf-8")
        if len(encoded) > 72:
            return False
        return bcrypt.checkpw(encoded, password_hash.encode())
    except ValueError:
        return False
