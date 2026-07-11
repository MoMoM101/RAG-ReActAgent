"""API authentication via admin token.

Lightweight admin-token protection for a single-user local knowledge base.
"""

import logging
import secrets as _secrets

from fastapi import HTTPException, Security
from fastapi.security import APIKeyHeader
from starlette.status import HTTP_401_UNAUTHORIZED

logger = logging.getLogger(__name__)

admin_token_header = APIKeyHeader(name="X-Admin-Token", auto_error=False)


async def require_admin(token: str | None = Security(admin_token_header)) -> None:
    """FastAPI dependency that requires a valid admin token.

    Raises 401 if the token is missing or does not match the configured value.
    Skips enforcement when ``admin_api_token`` is empty (opt-in security).
    """
    from config import settings

    if not settings.admin_api_token:
        # Token not configured — allow all access (backwards-compatible default)
        return

    if not token:
        raise HTTPException(
            status_code=HTTP_401_UNAUTHORIZED,
            detail="需要管理令牌（X-Admin-Token 请求头）",
            headers={"WWW-Authenticate": "X-Admin-Token"},
        )

    if not _secrets.compare_digest(token, settings.admin_api_token):
        raise HTTPException(
            status_code=HTTP_401_UNAUTHORIZED,
            detail="管理令牌无效",
        )


def generate_admin_token() -> str:
    """Generate a high-entropy admin token (64 hex chars)."""
    return _secrets.token_hex(32)
