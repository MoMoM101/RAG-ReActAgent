"""Auth endpoints: login, refresh, me."""

from datetime import UTC, datetime

from audit import record_audit
from auth.jwt import (
    create_access_token,
    create_refresh_token,
    decode_token,
    verify_password,
)
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from security import UserContext, get_current_user, jwt_auth
from sqlalchemy import select

from models.database import session_scope
from models.orm import User

router = APIRouter(prefix="/api/auth", tags=["auth"])


class LoginRequest(BaseModel):
    username: str
    password: str


class RefreshRequest(BaseModel):
    refresh_token: str = ""


@router.post("/login")
async def login(req: LoginRequest):
    from fastapi.responses import JSONResponse

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
        refresh_token = create_refresh_token(user.id)
        await record_audit("login_success",
                           object_id=user.id, detail=f"username={user.username}",
                           actor_id=user.id, actor_username=user.username)

        resp = JSONResponse({
            "access_token": access_token,
            "user": {
                "id": user.id,
                "username": user.username,
                "role": str(user.role),
            },
        })
        resp.set_cookie(
            key="refresh_token",
            value=refresh_token,
            httponly=True,
            samesite="lax",
            path="/api/auth",
            max_age=60 * 60 * 24 * 7,  # 7 days
        )
        return resp


@router.post("/refresh")
async def refresh(request: Request):
    token: str | None = None
    try:
        body = await request.json()
        token = body.get("refresh_token", "")
    except Exception:
        pass
    if not token:
        token = request.cookies.get("refresh_token")
    if not token:
        raise HTTPException(401, "No refresh token provided")

    try:
        payload = decode_token(token)
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

        access_token = create_access_token(user.id, user.username, str(user.role))
        return {"access_token": access_token}


@router.post("/logout")
async def logout():
    from fastapi.responses import JSONResponse
    resp = JSONResponse({"detail": "logged out"})
    resp.delete_cookie("refresh_token", path="/api/auth")
    return resp


class ChangePasswordRequest(BaseModel):
    current_password: str
    new_password: str


@router.post("/change-password")
async def change_password(req: ChangePasswordRequest, request: Request, _auth: None = Depends(jwt_auth)):
    """Change current user's password. Requires valid access token."""
    user_ctx: UserContext = get_current_user(request)

    if len(req.new_password.encode("utf-8")) < 12 or len(req.new_password.encode("utf-8")) > 72:
        raise HTTPException(400, "New password must be 12-72 bytes")

    async with session_scope() as session:
        result = await session.execute(
            select(User).where(User.id == user_ctx.user_id)
        )
        user = result.scalar_one_or_none()
        if not user or user.disabled:
            raise HTTPException(404, "User not found")

        if not verify_password(req.current_password, user.password_hash):
            raise HTTPException(400, "Current password is incorrect")

        from auth.jwt import hash_password
        user.password_hash = hash_password(req.new_password)
        await session.commit()

    return {"detail": "password changed"}


@router.get("/me")
async def me(request: Request, _auth: None = Depends(jwt_auth)):
    user: UserContext = get_current_user(request)
    return {
        "user_id": user.user_id,
        "username": user.username,
        "role": user.role,
    }
