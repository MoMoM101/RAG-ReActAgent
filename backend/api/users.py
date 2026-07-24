"""User management API — system_admin only."""

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import select

from audit import audit_from_request
from auth.jwt import hash_password
from models.database import session_scope
from models.orm import User, UserRole
from security import get_current_user, require_role

router = APIRouter(prefix="/api/users", tags=["users"])


class CreateUserRequest(BaseModel):
    username: str = Field(..., min_length=2, max_length=100)
    password: str = Field(..., min_length=12, max_length=72)
    role: str = "viewer"

    @field_validator("password")
    @classmethod
    def password_fits_bcrypt(cls, value: str) -> str:
        if len(value.encode("utf-8")) > 72:
            raise ValueError("password must not exceed 72 UTF-8 bytes")
        return value


class UpdateUserRequest(BaseModel):
    role: str | None = None
    disabled: int | None = None


@router.get("/")
async def list_users(
    request: Request,
    _auth: None = Depends(get_current_user),
    _enforce: None = require_role("system_admin"),
):
    async with session_scope() as session:
        result = await session.execute(select(User).order_by(User.created_at))
        users = result.scalars().all()
        return [
            {
                "id": u.id,
                "username": u.username,
                "role": str(u.role),
                "disabled": bool(u.disabled),
                "created_at": u.created_at.isoformat() if u.created_at else None,
                "last_login_at": u.last_login_at.isoformat() if u.last_login_at else None,
            }
            for u in users
        ]


@router.post("/", status_code=201)
async def create_user(
    req: CreateUserRequest,
    request: Request,
    _auth: None = Depends(get_current_user),
    _enforce: None = require_role("system_admin"),
):
    if req.role not in ("viewer", "editor", "knowledge_admin", "system_admin"):
        raise HTTPException(400, f"Invalid role: {req.role}")

    import uuid
    async with session_scope() as session:
        existing = (await session.execute(
            select(User).where(User.username == req.username)
        )).scalar_one_or_none()
        if existing:
            raise HTTPException(409, f"Username '{req.username}' already exists")

        user = User(
            id=str(uuid.uuid4()),
            username=req.username,
            password_hash=hash_password(req.password),
            role=req.role,
        )
        session.add(user)
        await session.commit()
        await audit_from_request(request, "user_create",
                                 object_type="user", object_id=user.id,
                                 detail=f"username={user.username}, role={user.role}")
        return {
            "id": user.id,
            "username": user.username,
            "role": str(user.role),
        }


@router.patch("/{user_id}")
async def update_user(
    user_id: str,
    req: UpdateUserRequest,
    request: Request,
    _auth: None = Depends(get_current_user),
    _enforce: None = require_role("system_admin"),
):
    async with session_scope() as session:
        user = (await session.execute(
            select(User).where(User.id == user_id)
        )).scalar_one_or_none()
        if not user:
            raise HTTPException(404, "User not found")

        if req.role is not None:
            if req.role not in ("viewer", "editor", "knowledge_admin", "system_admin"):
                raise HTTPException(400, f"Invalid role: {req.role}")
            user.role = UserRole(req.role)
        if req.disabled is not None:
            current = get_current_user(request)
            if user.id == current.user_id:
                raise HTTPException(400, "Cannot disable yourself")
            user.disabled = req.disabled

        await session.commit()
        await audit_from_request(request, "user_update",
                                 object_type="user", object_id=user_id,
                                 detail=f"changes={req.model_dump(exclude_none=True)}")
        return {
            "id": user.id,
            "username": user.username,
            "role": str(user.role),
            "disabled": bool(user.disabled),
        }


@router.delete("/{user_id}")
async def delete_user(
    user_id: str,
    request: Request,
    _auth: None = Depends(get_current_user),
    _enforce: None = require_role("system_admin"),
):
    current = get_current_user(request)
    if user_id == current.user_id:
        raise HTTPException(400, "Cannot delete yourself")

    async with session_scope() as session:
        user = (await session.execute(
            select(User).where(User.id == user_id)
        )).scalar_one_or_none()
        if not user:
            raise HTTPException(404, "User not found")
        await session.delete(user)
        await session.commit()
        await audit_from_request(request, "user_delete",
                                 object_type="user", object_id=user_id)
    return {"status": "deleted", "id": user_id}
