import asyncio
import json
import math
import uuid
from contextlib import suppress
from datetime import UTC

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse
from limiter import limiter
from pydantic import BaseModel, Field
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from agent.calculation_provenance import expression_uses_known_values, numeric_values
from agent.loop import run_agent_loop
from config import settings
from llm.base import ChatMessage, ToolCall
from models.database import get_db, session_scope
from models.orm import Conversation, Message

router = APIRouter(tags=["chat"])


class ChatRequest(BaseModel):
    conversation_id: str | None = None
    message: str = Field(..., max_length=10000)


def _schedule_session_extraction(
    conversation_id: str,
    *,
    user_message_id: str | None,
    delay_seconds: float,
) -> None:
    import worker.task_handlers  # noqa: F401  # ensure handler registration
    from worker.tasks import get_handler_registry, get_task_manager

    payload = {
        "conversation_id": conversation_id,
        "delay_seconds": delay_seconds,
    }
    get_task_manager().create(
        lambda: get_handler_registry().dispatch("extract_session_memories", payload),
        "extract_memories",
        metadata={"conv_id": conversation_id, "trigger": "answer_saved"},
        idempotency_key=(
            f"session-memory:v1:{conversation_id}:{user_message_id}"
            if user_message_id else None
        ),
        task_type="extract_session_memories",
        payload=payload,
    )


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


def _record_tool_result(tool_messages: list[dict], data: dict) -> None:
    """Attach a streamed result to the matching tool call."""
    if not tool_messages:
        return
    call_id = data.get("call_id")
    target = next(
        (
            item
            for item in reversed(tool_messages)
            if call_id and item.get("call_id") == call_id
        ),
        tool_messages[-1],
    )
    if data.get("success"):
        result_count = data.get("result_count")
        if isinstance(result_count, int) and not isinstance(result_count, bool):
            target["content"] = f"Success: {result_count} results"
        else:
            target["content"] = "Success"
    else:
        target["content"] = f"Error: {data.get('error', 'unknown')}"
    target["result_data"] = data.get("full_data")


def _calculator_results(
    tool_messages: list[dict],
    *,
    query: str = "",
    sources: list[dict] | None = None,
) -> list[int | float]:
    """Collect calculator outputs whose literal inputs have known provenance."""
    results: list[int | float] = []
    known_values = numeric_values(query)
    for source in sources or []:
        known_values.update(numeric_values(str(source.get("text", ""))))
    for message in tool_messages:
        if message.get("name") != "calculator" or message.get("content") != "Success":
            continue
        data = message.get("result_data")
        value = data.get("result") if isinstance(data, dict) else None
        args = message.get("args")
        expression = str(args.get("expression", "")) if isinstance(args, dict) else ""
        if (
            isinstance(value, int | float)
            and not isinstance(value, bool)
            and math.isfinite(float(value))
            and expression_uses_known_values(expression, known_values)
        ):
            results.append(value)
            known_values.add(float(value))
    return results


def _repair_post_stream_citations(
    answer: str,
    sources: list[dict],
    *,
    query: str,
    calculation_results: tuple[int | float, ...] = (),
):
    """Fill safe citation-only gaps left by incremental stream verification.

    Streaming verification evaluates atomic units as they arrive. The final
    markdown normalization can still expose a supported but uncited atomic
    claim. Reuse the deterministic repair policy before persisting/scoring the
    final answer; never invoke another model or rewrite factual content here.
    """
    from agent.grounding_repair import deterministic_repair
    from agent.verifier import Evidence, needs_grounding_repair, verify_answer

    verification = verify_answer(
        answer,
        sources,
        min_coverage=settings.grounding_min_coverage,
        query=query,
        calculation_results=calculation_results,
    )
    if (
        not settings.grounding_deterministic_repair_enabled
        or verification.citation_recall >= 1.0
    ):
        return answer, verification, False

    decision = needs_grounding_repair(
        answer,
        sources,
        query=query,
        calculation_results=calculation_results,
        coverage_recheck=False,
    )
    if decision.action != "deterministic_repair":
        return answer, verification, False

    evidence = [
        Evidence(
            citation_id=str(source.get("citation_id", "")),
            text=str(source.get("text", "")),
            document_key=str(source.get("document_key", "")),
            section_key=str(source.get("section_key", "")),
            filename=str(source.get("filename", "")),
        )
        for source in sources
        if source.get("citation_id") and source.get("text")
    ]
    if not evidence:
        return answer, verification, False

    repair = deterministic_repair(
        answer,
        evidence,
        decision,
        min_score=settings.grounding_auto_cite_min_score,
        min_margin=settings.grounding_auto_cite_min_margin,
        query=query,
    )
    if not repair.repaired or repair.repaired_text == answer:
        return answer, verification, False

    repaired_verification = verify_answer(
        repair.repaired_text,
        sources,
        min_coverage=settings.grounding_min_coverage,
        query=query,
        calculation_results=calculation_results,
    )
    improved = (
        repaired_verification.citation_recall > verification.citation_recall
        and repaired_verification.faithfulness >= verification.faithfulness
        and repaired_verification.citation_precision >= verification.citation_precision
        and repaired_verification.facts_supported >= verification.facts_supported
    )
    if not improved:
        return answer, verification, False
    return repair.repaired_text, repaired_verification, True


async def _save_messages(
    conv_id: str,
    assistant_content: str,
    tool_messages: list[dict],
    sources: list[dict] | None,
    *,
    preamble_content: str = "",
    verification: dict | None = None,
):
    """Persist assistant reply and tool messages after streaming completes.

    When tool calls happened, saves a separate assistant message (preamble) before
    the tool messages, so history loading can correctly reconstruct the tool_calls
    chain — without this, tool messages lack a preceding assistant, and the LLM
    API rejects the request on the next turn.
    """
    async with session_scope() as db:
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
            verification=json.dumps(verification, ensure_ascii=False) if verification else None,
        )
        db.add(assistant_msg)

        await db.commit()

    # Touch conversation updated_at
    async with session_scope() as db2:
        from datetime import datetime
        await db2.execute(
            update(Conversation).where(Conversation.id == conv_id).values(
                updated_at=datetime.now(UTC)
            )
        )
        await db2.commit()


async def sse_generator(user_message: str, history: list[ChatMessage], conv_id: str,
                       request: Request | None = None,
                       user_message_id: str | None = None):
    """SSE event stream that also collects messages for persistence.

    Monitors client disconnect via request.is_disconnected() and signals
    cancellation to the agent loop.
    """
    import logging
    _log = logging.getLogger(__name__)

    assistant_content = ""
    preamble_saved = ""  # content before first tool call (tool-calling preamble)
    tool_messages: list[dict] = []
    sources = None
    tool_phase = False
    skip_verification = False
    cancelled = asyncio.Event()

    from metrics import get_metrics
    get_metrics().record_sse_connection("open")

    async for event in run_agent_loop(
        user_message,
        history,
        cancelled=cancelled,
        conversation_id=conv_id,
        user_message_id=user_message_id,
    ):
        event_type = event["event"]
        get_metrics().record_stream_event(event_type)
        if event_type == "done":
            if assistant_content:
                from agent.answer_format import normalize_answer_markdown

                normalized_content = normalize_answer_markdown(assistant_content)
                if normalized_content != assistant_content:
                    assistant_content = normalized_content
                    yield (
                        "event: answer_replace\ndata: "
                        f"{json.dumps({'content': assistant_content}, ensure_ascii=False)}\n\n"
                    )
            verification_data = None
            if (
                assistant_content
                and sources
                and settings.grounding_verification_enabled
                and settings.grounding_enforcement != "off"
                and not skip_verification
            ):
                try:
                    calculation_results = tuple(_calculator_results(
                        tool_messages,
                        query=user_message,
                        sources=sources,
                    ))
                    assistant_content, verification, citations_repaired = (
                        _repair_post_stream_citations(
                            assistant_content,
                            sources,
                            query=user_message,
                            calculation_results=calculation_results,
                        )
                    )
                    if citations_repaired:
                        yield (
                            "event: answer_replace\ndata: "
                            f"{json.dumps({'content': assistant_content}, ensure_ascii=False)}\n\n"
                        )
                    verification_data = verification.to_dict(include_claims=True)
                    if (
                        settings.grounding_enforcement == "strict"
                        and verification.status != "verified"
                    ):
                        warning = "\n\n> ⚠️ 部分内容未获得检索来源的充分支持，请结合下方来源核对。"
                        assistant_content += warning
                        yield f"event: answer_chunk\ndata: {json.dumps({'delta': warning}, ensure_ascii=False)}\n\n"
                    yield f"event: verification\ndata: {json.dumps(verification_data, ensure_ascii=False)}\n\n"
                except Exception:
                    _log.warning("answer verification failed", exc_info=True)

            await _save_messages(
                conv_id,
                assistant_content,
                tool_messages,
                sources,
                preamble_content=preamble_saved,
                verification=verification_data,
            )
            if user_message_id:
                _schedule_session_extraction(
                    conv_id,
                    user_message_id=user_message_id,
                    delay_seconds=settings.memory_extract_debounce_seconds,
                )
            yield "event: done\ndata: {}\n\n"
            get_metrics().record_sse_connection("done")
            continue

        data = json.dumps(event["data"], ensure_ascii=False)
        yield f"event: {event_type}\ndata: {data}\n\n"

        # Collect for persistence
        if event_type == "answer_chunk":
            assistant_content += event["data"].get("delta", "")
        elif event_type == "sources":
            sources = event["data"]
        elif event_type == "timing":
            repair_reasons = event["data"].get("repair_reasons", [])
            skip_verification = bool({
                "empty_after_stream_verification",
                "generation_truncated",
                "reasoning_without_final",
                "empty_final_answer",
            } & set(repair_reasons))
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
        elif event_type == "tool_result" and tool_messages:
            # Parallel tools may finish out of order. Match the result to its
            # originating call instead of assigning it to the latest call.
            _record_tool_result(tool_messages, event["data"])

        # Check for client disconnect after each event
        if request is not None:
            try:
                if await request.is_disconnected():
                    _log.info("sse client disconnected conv_id=%s", conv_id)
                    cancelled.set()
                    # Save whatever we have so far
                    if assistant_content:
                        await _save_messages(
                            conv_id, assistant_content, tool_messages, sources,
                            preamble_content=preamble_saved,
                        )
                    return
            except Exception:
                pass  # Don't break the stream if disconnect check fails


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
        get_task_manager().create(
            lambda: extract_session_memories(conv_id),
            "extract_memories",
            metadata={"conv_id": conv_id},
            task_type="extract_session_memories",
            payload={"conversation_id": conv_id, "delay_seconds": 0},
        )
    else:
        # 新会话：对上一段会话做记忆提取
        from worker.tasks import get_task_manager

        from agent.session_extract import extract_session_memories
        prev = await db.execute(
            select(Conversation.id).order_by(Conversation.updated_at.desc()).limit(1)
        )
        prev_id = prev.scalar_one_or_none()
        if prev_id:
            get_task_manager().create(
                lambda: extract_session_memories(prev_id),
                "extract_memories",
                metadata={"conv_id": prev_id},
                task_type="extract_session_memories",
                payload={"conversation_id": prev_id, "delay_seconds": 0},
            )

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
                message_id=m.id,
            ))
            db_msg_by_idx[len(history) - 1] = m
        elif m.role == "assistant":
            history.append(ChatMessage(
                role="assistant", content=m.content, message_id=m.id,
            ))
        else:
            history.append(ChatMessage(
                role=m.role, content=m.content, message_id=m.id,
            ))

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
        sse_generator(
            req.message,
            history,
            conv_id,
            request,
            user_message_id=user_msg.id,
        ),
        media_type="text/event-stream",
        headers={
            "X-Conversation-Id": conv_id,
            "Cache-Control": "no-cache, no-transform",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
