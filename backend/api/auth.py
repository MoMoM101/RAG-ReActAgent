"""Auth endpoints: login, refresh, me."""

import uuid
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy import select

from auth.jwt import (
    create_access_token,
    create_refresh_token,
    decode_token,
    verify_password,
)
from models.database import async_session
from models.orm import User
from security import UserContext, get_current_user

router = APIRouter(prefix="/api/auth", tags=["auth"])


class LoginRequest(BaseModel):
    username: str
    password: str


class RefreshRequest(BaseModel):
    refresh_token: str


@router.post("/login")
async def login(req: LoginRequest):
    async with async_session() as session:
        result = await session.execute(
            select(User).where(User.username == req.username)
        )
        user = result.scalar_one_or_none()
        if not user or user.disabled:
            raise HTTPException(401, "Invalid credentials")
        if not verify_password(req.password, user.password_hash):
            raise HTTPException(401, "Invalid credentials")

        user.last_login_at = datetime.now(UTC)
        await session.commit()

        access_token = create_access_token(user.id, user.username, str(user.role))
        refresh_token = create_refresh_token(user.id)
        return {
            "access_token": access_token,
            "refresh_token": refresh_token,
            "user": {
                "id": user.id,
                "username": user.username,
                "role": str(user.role),
            },
        }


@router.post("/refresh")
async def refresh(req: RefreshRequest):
    try:
        payload = decode_token(req.refresh_token)
    except Exception:
        raise HTTPException(401, "Invalid or expired refresh token")
    if payload.get("type") != "refresh":
        raise HTTPException(401, "Not a refresh token")

    async with async_session() as session:
        result = await session.execute(
            select(User).where(User.id == payload["sub"])
        )
        user = result.scalar_one_or_none()
        if not user or user.disabled:
            raise HTTPException(401, "User not found or disabled")

        access_token = create_access_token(user.id, user.username, str(user.role))
        return {"access_token": access_token}


@router.get("/me")
async def me(request: Request, _auth: None = Depends(get_current_user)):
    user: UserContext = request.state.user
    return {
        "user_id": user.user_id,
        "username": user.username,
        "role": user.role,
    }
