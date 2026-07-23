"""Rolling working-context persistence tests."""

import uuid

from agent.context_state import (
    WorkingContextState,
    compact_working_context,
    format_working_context,
    load_working_context,
    merge_summary,
)
from agent.loop import _pending_dropped_messages
from agent.token_counter import TiktokenCounter
from models.database import session_scope
from models.orm import Conversation


def test_merge_summary_is_deduplicated_structured_and_bounded():
    counter = TiktokenCounter("gpt-4o")
    queries = [
        "必须保留来源引用",
        "必须保留来源引用",
        "决定先用单租户方案",
        "工作上下文如何存储？",
    ] + [f"很长的历史事项 {index} " * 20 for index in range(20)]

    summary, token_count = merge_summary(
        None,
        queries,
        [f"message-{index}" for index in range(30)],
        counter=counter,
        max_tokens=160,
        max_items=5,
    )

    assert summary["constraints"].count("必须保留来源引用") <= 1
    assert len(summary["source_message_ids"]) <= 5
    assert token_count <= 160
    assert counter.count_text(format_working_context(summary)) <= 160


def test_pending_dropped_messages_uses_persisted_watermark():
    state = WorkingContextState(
        summary={"source_message_ids": ["message-1"]},
        through_message_id="message-2",
        source_hash="hash",
        token_count=10,
        version=1,
    )

    queries, message_ids = _pending_dropped_messages(
        ["old one", "old two", "new three"],
        ["message-1", "message-2", "message-3"],
        state,
    )

    assert queries == ["new three"]
    assert message_ids == ["message-3"]


def test_pending_dropped_messages_falls_back_to_known_ids():
    state = WorkingContextState(
        summary={"source_message_ids": ["message-1"]},
        through_message_id="evicted-watermark",
        source_hash="hash",
        token_count=10,
        version=1,
    )

    queries, message_ids = _pending_dropped_messages(
        ["known", "new"],
        ["message-1", "message-2"],
        state,
    )

    assert queries == ["new"]
    assert message_ids == ["message-2"]


async def test_compaction_is_idempotent_and_updates_in_place():
    conversation_id = str(uuid.uuid4())
    async with session_scope() as session:
        session.add(Conversation(id=conversation_id, title="context test"))
        await session.commit()

    first = await compact_working_context(
        conversation_id,
        ["决定采用单租户", "还需要检查什么？"],
        ["message-1", "message-2"],
    )
    repeated = await compact_working_context(
        conversation_id,
        ["决定采用单租户", "还需要检查什么？"],
        ["message-1", "message-2"],
    )
    second = await compact_working_context(
        conversation_id,
        ["必须限制摘要大小"],
        ["message-3"],
    )
    loaded = await load_working_context(conversation_id)

    assert first is not None and first.version == 1
    assert repeated is not None and repeated.version == 1
    assert second is not None and second.version == 2
    assert loaded == second
    assert "必须限制摘要大小" in loaded.summary["constraints"]
