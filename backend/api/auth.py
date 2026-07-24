"""Auth endpoints: login, refresh, me."""

from datetime import UTC, datetime

from audit import record_audit
from auth.jwt import (
    create_access_token,
    create_refresh_token,
    credential_version,
    decode_token,
    hash_password,
    verify_password,
)
from fastapi import APIRouter, Depends, HTTPException, Request, Response
from limiter import limiter
from pydantic import BaseModel, Field
from security import UserContext, get_current_user, jwt_auth
from sqlalchemy import select

from config import settings
from models.database import session_scope
from models.orm import User

router = APIRouter(prefix="/api/auth", tags=["auth"])
REFRESH_COOKIE_NAME = "rag_refresh_token"


class LoginRequest(BaseModel):
    username: str = Field(min_length=3, max_length=64)
    password: str = Field(min_length=1)


class ChangePasswordRequest(BaseModel):
    current_password: str = Field(min_length=1)
    new_password: str = Field(min_length=1)


def _set_refresh_cookie(response: Response, refresh_token: str) -> None:
    response.set_cookie(
        key=REFRESH_COOKIE_NAME,
        value=refresh_token,
        max_age=settings.jwt_refresh_token_expire_days * 24 * 60 * 60,
        path="/api/auth",
        secure=settings.auth_cookie_secure,
        httponly=True,
        samesite="lax",
    )


def _clear_refresh_cookie(response: Response) -> None:
    response.delete_cookie(
        key=REFRESH_COOKIE_NAME,
        path="/api/auth",
        secure=settings.auth_cookie_secure,
        httponly=True,
        samesite="lax",
    )


@router.post("/login")
@limiter.limit("10/minute")
async def login(request: Request, response: Response, req: LoginRequest):
    async with session_scope() as session:
        result = await session.execute(
            select(User).where(User.username == req.username)
        )
        user = result.scalar_one_or_none()
        if not user or user.disabled:
            await record_audit("login_failure", result="failure",
                               detail=f"username={req.username}")
            raise HTTPException(401, "Invalid credentials")
        if not verify_password(req.password, user.password_hash):
            await record_audit("login_failure", result="failure",
                               detail=f"username={req.username}")
            raise HTTPException(401, "Invalid credentials")

        user.last_login_at = datetime.now(UTC)
        await session.commit()

        access_token = create_access_token(user.id, user.username, str(user.role))
        refresh_token = create_refresh_token(user.id, user.password_hash)
        _set_refresh_cookie(response, refresh_token)
        await record_audit("login_success",
                           object_id=user.id, detail=f"username={user.username}",
                           actor_id=user.id, actor_username=user.username)
        return {
            "access_token": access_token,
            "user": {
                "id": user.id,
                "username": user.username,
                "role": str(user.role),
            },
        }


@router.post("/refresh")
@limiter.limit("30/minute")
async def refresh(request: Request, response: Response):
    refresh_token = request.cookies.get(REFRESH_COOKIE_NAME)
    if not refresh_token:
        raise HTTPException(401, "Refresh cookie is missing")
    try:
        payload = decode_token(refresh_token)
    except Exception as exc:
        raise HTTPException(401, "Invalid or expired refresh token") from exc
    if payload.get("type") != "refresh":
        raise HTTPException(401, "Not a refresh token")

    async with session_scope() as session:
        result = await session.execute(
            select(User).where(User.id == payload["sub"])
        )
        user = result.scalar_one_or_none()
        if not user or user.disabled:
            raise HTTPException(401, "User not found or disabled")
        if payload.get("credential_version") != credential_version(
            user.password_hash
        ):
            raise HTTPException(401, "Refresh token is no longer valid")

        rotated_refresh_token = create_refresh_token(user.id, user.password_hash)
        _set_refresh_cookie(response, rotated_refresh_token)
        return {
            "access_token": create_access_token(
                user.id,
                user.username,
                str(user.role),
            ),
        }


@router.post("/logout", status_code=204)
async def logout(response: Response):
    _clear_refresh_cookie(response)


@router.get("/me")
async def me(
    request: Request,
    _auth: None = Depends(jwt_auth),
):
    user: UserContext = get_current_user(request)
    return {
        "user_id": user.user_id,
        "username": user.username,
        "role": user.role,
    }


@router.post("/change-password")
@limiter.limit("5/minute")
async def change_password(
    request: Request,
    response: Response,
    req: ChangePasswordRequest,
    _auth: None = Depends(jwt_auth),
):
    context = get_current_user(request)
    async with session_scope() as session:
        result = await session.execute(select(User).where(User.id == context.user_id))
        user = result.scalar_one_or_none()
        if not user or user.disabled:
            raise HTTPException(401, "User not found or disabled")
        if not verify_password(req.current_password, user.password_hash):
            raise HTTPException(400, "Current password is incorrect")
        if verify_password(req.new_password, user.password_hash):
            raise HTTPException(400, "New password must differ from current password")

        user.password_hash = hash_password(req.new_password)
        await session.commit()
        await record_audit(
            "password_change",
            object_id=user.id,
            actor_id=user.id,
            actor_username=user.username,
        )
        _set_refresh_cookie(
            response,
            create_refresh_token(user.id, user.password_hash),
        )
        return {
            "access_token": create_access_token(user.id, user.username, str(user.role)),
            "user": {
                "id": user.id,
                "username": user.username,
                "role": str(user.role),
            },
        }
