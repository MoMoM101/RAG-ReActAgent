"""Audit log query API — system_admin only."""

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy import text as sa_text

from models.database import session_scope
from security import get_current_user, require_role

router = APIRouter(prefix="/api/audit", tags=["audit"])


@router.get("/")
async def list_audit_logs(
    request: Request,
    action: str = Query(default=""),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    _auth: None = Depends(get_current_user),
    _enforce: None = require_role("system_admin"),
):
    async with session_scope() as session:
        conn = await session.connection()
        if action:
            rows = (await conn.execute(sa_text(
                "SELECT id, actor_id, actor_username, action, object_type, "
                "object_id, result, detail, request_id, created_at "
                "FROM audit_logs WHERE action=:action "
                "ORDER BY id DESC LIMIT :limit OFFSET :offset"
            ), {"action": action, "limit": limit, "offset": offset})).fetchall()
        else:
            rows = (await conn.execute(sa_text(
                "SELECT id, actor_id, actor_username, action, object_type, "
                "object_id, result, detail, request_id, created_at "
                "FROM audit_logs ORDER BY id DESC LIMIT :limit OFFSET :offset"
            ), {"limit": limit, "offset": offset})).fetchall()

        return [
            {
                "id": r[0],
                "actor_id": r[1],
                "actor_username": r[2],
                "action": r[3],
                "object_type": r[4],
                "object_id": r[5],
                "result": r[6],
                "detail": r[7],
                "request_id": r[8],
                "created_at": r[9],
            }
            for r in rows
        ]
