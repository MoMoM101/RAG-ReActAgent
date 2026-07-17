"""Audit logging — record high-risk operations with actor identity."""

import logging

from sqlalchemy import text as sa_text

from models.database import async_session

logger = logging.getLogger(__name__)


async def record_audit(
    action: str,
    object_type: str = "",
    object_id: str = "",
    result: str = "success",
    detail: str = "",
    *,
    actor_id: str = "anonymous",
    actor_username: str = "anonymous",
    request_id: str = "",
) -> None:
    """Write an audit record. Best-effort — never raises."""
    try:
        async with async_session() as session:
            conn = await session.connection()
            await conn.execute(sa_text(
                "INSERT INTO audit_logs "
                "(actor_id, actor_username, action, object_type, object_id, result, detail, request_id) "
                "VALUES (:aid, :aun, :act, :ot, :oid, :res, :det, :rid)"
            ), {
                "aid": actor_id,
                "aun": actor_username,
                "act": action,
                "ot": object_type,
                "oid": object_id,
                "res": result,
                "det": detail,
                "rid": request_id,
            })
            await session.commit()
    except Exception as e:
        logger.warning("audit record failed action=%s: %s", action, e)


def audit_from_request(request, action: str, **kwargs):
    """Convenience: extract actor and request_id from FastAPI Request."""
    user = getattr(request.state, "user", None)
    actor_id = user.user_id if user else "anonymous"
    actor_username = user.username if user else "anonymous"
    rid = getattr(request.state, "request_id", "")
    # Return coroutine — caller must await
    return record_audit(
        action=action,
        actor_id=actor_id,
        actor_username=actor_username,
        request_id=rid,
        **kwargs,
    )
