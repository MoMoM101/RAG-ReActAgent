"""API for listing and updating user memories."""

from datetime import UTC

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

router = APIRouter(prefix="/api/memories", tags=["memories"])


class UpdateMemoryRequest(BaseModel):
    content: str


def _flatten_profile(profile: dict) -> list[dict]:
    """展开画像为带 id 的扁平列表，供前端展示。"""
    from datetime import datetime

    now = datetime.now(UTC).isoformat()
    items = []
    if profile.get("name"):
        items.append({"id": "name:0", "content": f"用户名: {profile['name']}",
                       "memory_type": "identity", "deprecated": False,
                       "conversation_id": None, "created_at": now})
    if profile.get("role"):
        items.append({"id": "role:0", "content": f"用户是{profile['role']}",
                       "memory_type": "identity", "deprecated": False,
                       "conversation_id": None, "created_at": now})
    for i, p in enumerate(profile.get("preferences", [])):
        items.append({"id": f"preference:{i}", "content": p,
                       "memory_type": "preference", "deprecated": False,
                       "conversation_id": None, "created_at": now})

    for i, d in enumerate(profile.get("decisions", [])):
        items.append({"id": f"decision:{i}", "content": d,
                       "memory_type": "decision", "deprecated": False,
                       "conversation_id": None, "created_at": now})

    for i, f in enumerate(profile.get("facts", [])):
        c = f["content"] if isinstance(f, dict) else f
        ts = f.get("ts", now) if isinstance(f, dict) else now
        items.append({"id": f"fact:{i}", "content": c,
                       "memory_type": "fact", "deprecated": False,
                       "conversation_id": None, "created_at": ts})
    return items


@router.get("")
async def list_memories(
    memory_type: str | None = Query(None),
    include_deprecated: bool = Query(False),
):
    from memory.profile import get_profile
    profile = await get_profile()
    items = _flatten_profile(profile)
    if memory_type:
        items = [i for i in items if i["memory_type"] == memory_type]
    return {"memories": items}


@router.get("/profile")
async def get_profile():
    from memory.profile import get_profile as _get
    return await _get()


@router.put("/{memory_id}")
async def update_memory(memory_id: str, body: UpdateMemoryRequest):
    from memory.profile import update_item
    result = await update_item(memory_id, body.content)
    if result is None:
        raise HTTPException(404, "Memory not found")
    return {"content": body.content}


@router.delete("/{memory_id}")
async def delete_memory(memory_id: str):
    from memory.profile import delete_item
    result = await delete_item(memory_id)
    if result is None:
        raise HTTPException(404, "Memory not found")
    return {"status": "deleted"}


@router.delete("")
async def clear_all_memories():
    from sqlalchemy import delete, select

    from models.database import session_scope
    from models.orm import UserMemory
    from vectordb.qdrant import QdrantVectorDB

    async with session_scope() as session:
        result = await session.execute(select(UserMemory.id))
        all_ids = [r[0] for r in result.fetchall()]
        if all_ids:
            vdb = QdrantVectorDB(collection_name="user_memories")
            await vdb.delete_by_ids(all_ids)
            await session.execute(delete(UserMemory))
            await session.commit()

    from memory.profile import _empty, _save
    await _save(_empty())
    return {"status": "cleared", "count": len(all_ids)}
