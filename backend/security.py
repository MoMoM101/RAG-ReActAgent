"""JWT-based authentication and role-based access control.

Replaces the legacy single admin-token model.  When legacy_admin_token_enabled
is True the old X-Admin-Token header is still accepted (with a deprecation
warning) so existing E2E and CI scripts continue to work during the transition.
"""

import logging
import secrets as _secrets
from dataclasses import dataclass

from fastapi import Depends, HTTPException, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from starlette.status import HTTP_401_UNAUTHORIZED, HTTP_403_FORBIDDEN

from config import settings

logger = logging.getLogger(__name__)

bearer_scheme = HTTPBearer(auto_error=False)


def generate_admin_token() -> str:
    """Return a cryptographically secure token for first-run administration."""
    return _secrets.token_urlsafe(32)


@dataclass
class UserContext:
    user_id: str
    username: str
    role: str


# ── JWT auth dependency ───────────────────────────────────────────

async def jwt_auth(request: Request) -> None:
    """FastAPI dependency: verify JWT and inject request.state.user.

    Falls back to legacy X-Admin-Token when enabled.
    """
    # Try JWT first
    auth: HTTPAuthorizationCredentials | None = await bearer_scheme(request)
    if auth and auth.scheme.lower() == "bearer":
        token = auth.credentials
        try:
            from auth.jwt import decode_token
            payload = decode_token(token)
            if payload.get("type") != "access":
                raise HTTPException(
                    status_code=HTTP_401_UNAUTHORIZED,
                    detail="Not an access token",
                )
            request.state.user = UserContext(
                user_id=payload["sub"],
                username=payload["username"],
                role=payload["role"],
            )
            return
        except HTTPException:
            raise
        except Exception as exc:
            logger.warning("JWT verification failed: %s", exc)
            raise HTTPException(
                status_code=HTTP_401_UNAUTHORIZED,
                detail="Invalid or expired token",
            ) from exc

    # Fall back to legacy X-Admin-Token
    if settings.legacy_admin_token_enabled and settings.admin_api_token:
        token = request.headers.get("X-Admin-Token", "")
        if token and _secrets.compare_digest(token, settings.admin_api_token):
            logger.warning(
                "Legacy X-Admin-Token used — this will be removed. "
                "Migrate to Bearer JWT."
            )
            request.state.user = UserContext(
                user_id="legacy_admin",
                username="admin",
                role="system_admin",
            )
            return

    raise HTTPException(
        status_code=HTTP_401_UNAUTHORIZED,
        detail="Authentication required",
        headers={"WWW-Authenticate": "Bearer"},
    )


# ── Role enforcement ───────────────────────────────────────────────

def require_role(*roles: str):
    """Dependency factory: only allow the given roles."""

    async def _enforce(request: Request) -> None:
        user = get_current_user(request)
        if user.role not in roles:
            raise HTTPException(
                status_code=HTTP_403_FORBIDDEN,
                detail=f"Role '{user.role}' not in {roles}",
            )

    return Depends(_enforce)


# ── Backward compat (for existing code + test fixtures) ───────────

async def require_admin(
    request: Request,
    _auth: None = Depends(jwt_auth),
) -> None:
    """Drop-in replacement for old require_admin.  Enforces system_admin role."""
    user = get_current_user(request)
    if user.role != "system_admin":
        raise HTTPException(
            status_code=HTTP_403_FORBIDDEN,
            detail="system_admin role required",
        )


def get_current_user(request: Request) -> UserContext:
    """Retrieve the authenticated user from request state."""
    if not hasattr(request.state, "user"):
        raise HTTPException(
            status_code=HTTP_401_UNAUTHORIZED,
            detail="Not authenticated",
        )
    return request.state.user
