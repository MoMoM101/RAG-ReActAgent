import json
import uuid

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from agent.tool_summary import summarize_tool_result
from models.database import get_db
from models.orm import Conversation, Message

router = APIRouter(prefix="/api/conversations", tags=["conversations"])


class CreateConversationRequest(BaseModel):
    title: str = "New Chat"


@router.get("")
async def list_conversations(db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        select(Conversation).order_by(Conversation.updated_at.desc())
    )
    convs = result.scalars().all()
    return [
        {
            "id": c.id,
            "title": c.title,
            "created_at": c.created_at.isoformat(),
            "updated_at": c.updated_at.isoformat(),
        }
        for c in convs
    ]


@router.post("")
async def create_conversation(req: CreateConversationRequest, db: AsyncSession = Depends(get_db)):
    conv = Conversation(id=str(uuid.uuid4()), title=req.title)
    db.add(conv)
    await db.commit()
    return {"id": conv.id, "title": conv.title}


@router.delete("")
async def delete_all_conversations(db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Conversation))
    convs = result.scalars().all()
    count = len(convs)
    await db.execute(delete(Message))
    await db.execute(delete(Conversation))
    await db.commit()
    return {"status": "deleted", "count": count}


@router.delete("/{conv_id}")
async def delete_conversation(conv_id: str, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Conversation).where(Conversation.id == conv_id))
    conv = result.scalar_one_or_none()
    if not conv:
        raise HTTPException(404, "Conversation not found")
    # Also delete associated messages
    await db.execute(delete(Message).where(Message.conversation_id == conv_id))
    await db.delete(conv)
    await db.commit()
    return {"status": "deleted", "id": conv_id}


class RenameRequest(BaseModel):
    title: str


@router.patch("/{conv_id}")
async def rename_conversation(conv_id: str, req: RenameRequest, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Conversation).where(Conversation.id == conv_id))
    conv = result.scalar_one_or_none()
    if not conv:
        raise HTTPException(404, "Conversation not found")
    conv.title = req.title[:100]
    await db.commit()
    return {"id": conv.id, "title": conv.title}


@router.get("/{conv_id}/context")
async def get_conversation_context(conv_id: str, db: AsyncSession = Depends(get_db)):
    """Expose bounded context state and exact counters without raw source IDs."""
    result = await db.execute(select(Conversation).where(Conversation.id == conv_id))
    conv = result.scalar_one_or_none()
    if not conv:
        raise HTTPException(404, "Conversation not found")
    try:
        summary = json.loads(conv.context_summary) if conv.context_summary else {}
    except (json.JSONDecodeError, TypeError):
        summary = {}
    source_ids = summary.pop("source_message_ids", [])
    return {
        "conversation_id": conv.id,
        "summary": summary,
        "source_message_count": len(source_ids) if isinstance(source_ids, list) else 0,
        "through_message_id": conv.context_summary_through_id,
        "token_count": conv.context_summary_token_count,
        "version": conv.context_summary_version,
        "updated_at": (
            conv.context_summary_updated_at.isoformat()
            if conv.context_summary_updated_at else None
        ),
    }


@router.get("/{conv_id}/messages")
async def get_messages(conv_id: str, db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        select(Message)
        .where(Message.conversation_id == conv_id)
        .order_by(Message.created_at.asc())
    )
    msgs = result.scalars().all()
    response = []
    for m in msgs:
        result_data = None
        if m.tool_result_json:
            try:
                result_data = json.loads(m.tool_result_json)
            except (json.JSONDecodeError, TypeError):
                result_data = None
        response.append({
            "id": m.id,
            "role": m.role,
            "content": m.content,
            "tool_name": m.tool_name,
            "tool_call_id": m.tool_call_id,
            "tool_args": m.tool_args,
            "tool_result_summary": (
                summarize_tool_result(m.tool_name or "", result_data)
                if m.role == "tool" and result_data is not None
                else None
            ),
            "sources": m.sources,
            "verification": m.verification,
            "created_at": m.created_at.isoformat(),
        })
    return response
