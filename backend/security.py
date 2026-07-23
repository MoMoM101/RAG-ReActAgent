"""JWT authentication and role-based access control."""

import logging
from dataclasses import dataclass

from fastapi import Depends, HTTPException, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from starlette.status import HTTP_401_UNAUTHORIZED, HTTP_403_FORBIDDEN

logger = logging.getLogger(__name__)

bearer_scheme = HTTPBearer(auto_error=False)


@dataclass
class UserContext:
    user_id: str
    username: str
    role: str


# ── JWT auth dependency ───────────────────────────────────────────

async def jwt_auth(request: Request) -> None:
    """Verify Bearer authentication."""
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


async def require_admin(
    request: Request,
    _auth: None = Depends(jwt_auth),
) -> None:
    """Require a valid JWT whose role is ``system_admin``."""
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
