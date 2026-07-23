"""Durable background-task handlers registered for restart recovery."""

import asyncio

from worker.tasks import get_handler_registry

registry = get_handler_registry()


@registry.register("compact_context")
async def compact_context_handler(payload: dict) -> None:
    from agent.context_state import compact_working_context

    await compact_working_context(
        str(payload["conversation_id"]),
        [str(item) for item in payload.get("queries", [])],
        [str(item) for item in payload.get("message_ids", [])],
    )


@registry.register("process_dropped_memories")
async def process_dropped_memories_handler(payload: dict) -> None:
    from agent.loop import _process_dropped

    await _process_dropped([str(item) for item in payload.get("queries", [])])


@registry.register("extract_session_memories")
async def extract_session_memories_handler(payload: dict) -> None:
    from agent.session_extract import extract_session_memories

    delay = min(max(float(payload.get("delay_seconds", 0)), 0.0), 60.0)
    if delay:
        await asyncio.sleep(delay)
    await extract_session_memories(str(payload["conversation_id"]))
