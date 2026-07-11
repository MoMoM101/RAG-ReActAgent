import json
import uuid
from contextlib import suppress
from datetime import UTC

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse
from limiter import limiter
from pydantic import BaseModel, Field
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from agent.loop import run_agent_loop
from llm.base import ChatMessage, ToolCall
from models.database import async_session, get_db
from models.orm import Conversation, Message

router = APIRouter(tags=["chat"])


class ChatRequest(BaseModel):
    conversation_id: str | None = None
    message: str = Field(..., max_length=10000)


def _truncate_tool_result(data: dict, max_chars: int = 4000) -> str:
    """Serialize tool result data, truncating long text fields to avoid DB bloat."""
    if not data:
        return "{}"

    truncated = dict(data)
    if "results" in truncated:
        items = truncated["results"]
        if isinstance(items, list):
            truncated["results"] = items[:3]  # keep top 3 only
            for item in truncated["results"]:
                if isinstance(item, dict) and "text" in item:
                    item["text"] = item["text"][:300]

    result_json = json.dumps(truncated, ensure_ascii=False, default=str)
    if len(result_json) > max_chars:
        result_json = result_json[:max_chars - 3] + "..."
    return result_json


def _tool_message_content(m: "Message") -> str:
    """Return the best available tool result content for history replay.

    Prefers full tool_result_json when available, falls back to summary content.
    """
    if m.tool_result_json:
        return f"[历史工具结果: {m.tool_name}]\n{m.tool_result_json}"
    return m.content or ""


async def _save_messages(
    conv_id: str,
    assistant_content: str,
    tool_messages: list[dict],
    sources: list[dict] | None,
    *,
    preamble_content: str = "",
):
    """Persist assistant reply and tool messages after streaming completes.

    When tool calls happened, saves a separate assistant message (preamble) before
    the tool messages, so history loading can correctly reconstruct the tool_calls
    chain — without this, tool messages lack a preceding assistant, and the LLM
    API rejects the request on the next turn.
    """
    async with async_session() as db:
        # If tools were called, always save the preamble as a tool-calling assistant
        # message first, even if the LLM output no text before the tool call.
        if tool_messages:
            pre_msg = Message(
                id=str(uuid.uuid4()),
                conversation_id=conv_id,
                role="assistant",
                content=preamble_content or "",
            )
            db.add(pre_msg)

        # Save tool messages with call_id, args, and full result data
        for tm in tool_messages:
            result_data = tm.get("result_data")
            tool_msg = Message(
                id=str(uuid.uuid4()),
                conversation_id=conv_id,
                role="tool",
                content=tm["content"],
                tool_call_id=tm.get("call_id"),
                tool_name=tm["name"],
                tool_args=json.dumps(tm.get("args", {}), ensure_ascii=False),
                tool_result_json=_truncate_tool_result(result_data) if result_data else None,
            )
            db.add(tool_msg)

        # Save final assistant message
        assistant_msg = Message(
            id=str(uuid.uuid4()),
            conversation_id=conv_id,
            role="assistant",
            content=assistant_content,
            sources=json.dumps(sources, ensure_ascii=False) if sources else None,
        )
        db.add(assistant_msg)

        await db.commit()

    # Touch conversation updated_at
    async with async_session() as db2:
        from datetime import datetime
        await db2.execute(
            update(Conversation).where(Conversation.id == conv_id).values(
                updated_at=datetime.now(UTC)
            )
        )
        await db2.commit()


async def sse_generator(user_message: str, history: list[ChatMessage], conv_id: str):
    """SSE event stream that also collects messages for persistence."""
    assistant_content = ""
    preamble_saved = ""  # content before first tool call (tool-calling preamble)
    tool_messages: list[dict] = []
    sources = None
    tool_phase = False

    async for event in run_agent_loop(user_message, history):
        event_type = event["event"]
        data = json.dumps(event["data"], ensure_ascii=False)
        yield f"event: {event_type}\ndata: {data}\n\n"

        # Collect for persistence
        if event_type == "answer_chunk":
            assistant_content += event["data"].get("delta", "")
        elif event_type == "sources":
            sources = event["data"]
        elif event_type == "tool_call":
            if not tool_phase:
                tool_phase = True
                preamble_saved = assistant_content
                assistant_content = ""
            tool_messages.append({
                "name": event["data"].get("tool", "unknown"),
                "call_id": event["data"].get("call_id"),
                "args": event["data"].get("args", {}),
                "content": "",  # filled by tool_result
            })
        elif event_type == "tool_result":
            # Update the last tool message with result content and full data
            if tool_messages:
                d = event["data"]
                if d.get("success"):
                    tool_messages[-1]["content"] = f"Success: {d.get('result_count', 0)} results"
                else:
                    tool_messages[-1]["content"] = f"Error: {d.get('error', 'unknown')}"
                tool_messages[-1]["result_data"] = d.get("full_data")
        elif event_type == "done":
            await _save_messages(
                conv_id, assistant_content, tool_messages, sources,
                preamble_content=preamble_saved,
            )


@router.post("/api/chat")
@limiter.limit("30/minute")
async def chat(request: Request, req: ChatRequest, db: AsyncSession = Depends(get_db)):
    # Load or create conversation
    if req.conversation_id:
        result = await db.execute(
            select(Conversation).where(Conversation.id == req.conversation_id)
        )
        conv = result.scalar_one_or_none()
        if not conv:
            raise HTTPException(404, "Conversation not found")
        conv_id = conv.id
        # 打开旧会话：增量提取未处理的消息
        from worker.tasks import get_task_manager

        from agent.session_extract import extract_session_memories
        get_task_manager().create(extract_session_memories(conv_id), "extract_memories", metadata={"conv_id": conv_id})
    else:
        # 新会话：对上一段会话做记忆提取
        from worker.tasks import get_task_manager

        from agent.session_extract import extract_session_memories
        prev = await db.execute(
            select(Conversation.id).order_by(Conversation.updated_at.desc()).limit(1)
        )
        prev_id = prev.scalar_one_or_none()
        if prev_id:
            get_task_manager().create(extract_session_memories(prev_id), "extract_memories", metadata={"conv_id": prev_id})

        conv = Conversation(id=str(uuid.uuid4()), title=req.message[:50])
        db.add(conv)
        await db.commit()
        conv_id = conv.id

    # Load history
    hist_result = await db.execute(
        select(Message)
        .where(Message.conversation_id == conv_id)
        .order_by(Message.created_at.asc())
    )
    messages_db: list[Message] = list(hist_result.scalars().all())

    # Pass 1: build history list, keeping a map of message index for args lookup
    history: list[ChatMessage] = []
    db_msg_by_idx: dict[int, Message] = {}  # for looking up tool_args later
    for _idx, m in enumerate(messages_db):
        if m.role == "tool":
            history.append(ChatMessage(
                role="tool", content=_tool_message_content(m),
                tool_call_id=m.tool_call_id, tool_name=m.tool_name,
            ))
            db_msg_by_idx[len(history) - 1] = m
        elif m.role == "assistant":
            history.append(ChatMessage(role="assistant", content=m.content))
        else:
            history.append(ChatMessage(role=m.role, content=m.content))

    # Pass 2: link tool messages back to their preceding assistant
    for i in range(len(history) - 1, -1, -1):
        msg = history[i]
        if msg.role == "tool" and msg.tool_call_id and msg.tool_name:
            # Restore original tool call args from DB record
            args = {}
            db_msg = db_msg_by_idx.get(i)
            if db_msg and db_msg.tool_args:
                with suppress(json.JSONDecodeError):
                    args = json.loads(db_msg.tool_args)
            for j in range(i - 1, -1, -1):
                if history[j].role == "assistant":
                    target_msg = history[j]
                    if target_msg.tool_calls is None:
                        target_msg.tool_calls = []
                    target_msg.tool_calls.insert(0, ToolCall(
                        id=msg.tool_call_id,
                        name=msg.tool_name,
                        arguments=args,
                    ))
                    break

    # Save user message
    user_msg = Message(
        id=str(uuid.uuid4()),
        conversation_id=conv_id,
        role="user",
        content=req.message,
    )
    db.add(user_msg)
    await db.commit()

    # Touch conversation updated_at
    from datetime import datetime
    await db.execute(
        update(Conversation).where(Conversation.id == conv_id).values(
            updated_at=datetime.now(UTC)
        )
    )
    await db.commit()

    return StreamingResponse(
        sse_generator(req.message, history, conv_id),
        media_type="text/event-stream",
        headers={
            "X-Conversation-Id": conv_id,
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
        },
    )
