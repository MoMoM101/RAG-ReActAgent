import uuid

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

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


@router.get("/{conv_id}/messages")
async def get_messages(conv_id: str, db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        select(Message)
        .where(Message.conversation_id == conv_id)
        .order_by(Message.created_at.asc())
    )
    msgs = result.scalars().all()
    return [
        {
            "id": m.id,
            "role": m.role,
            "content": m.content,
            "tool_name": m.tool_name,
            "tool_call_id": m.tool_call_id,
            "tool_args": m.tool_args,
            "sources": m.sources,
            "created_at": m.created_at.isoformat(),
        }
        for m in msgs
    ]
