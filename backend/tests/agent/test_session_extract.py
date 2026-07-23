"""Session-end memory extraction tests — _extract_with_llm and extract_session_memories."""

import uuid
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, patch

import pytest

from llm.base import LLMResponse


def _make_tool_call(name, args, call_id="call_1"):
    from llm.base import ToolCall
    return ToolCall(id=call_id, name=name, arguments=args)


class TestExtractWithLLM:
    def test_recent_line_truncation_is_token_bounded(self):
        from agent.session_extract import _truncate_recent_lines
        from agent.token_counter import get_token_counter
        from config import settings

        text = "\n".join(f"[user] 历史消息 {index} " * 20 for index in range(30))
        truncated = _truncate_recent_lines(text, 100)
        counter = get_token_counter(
            settings.llm_model,
            settings.tokenizer_provider,
            settings.tokenizer_model,
            settings.tokenizer_fallback_safety_factor,
        )

        assert counter.count_text(truncated) <= 100
        assert "历史消息 29" in truncated
        assert "历史消息 0" not in truncated

    @pytest.mark.asyncio
    async def test_extract_valid_json_array(self, make_fake_llm):
        """LLM returns valid JSON array → parsed correctly."""
        make_fake_llm([
            [LLMResponse(content='[{"content":"user is Python dev","memory_type":"identity"}]')],
        ])

        from agent.session_extract import _extract_with_llm
        result = await _extract_with_llm("[user] I am a Python developer")

        assert len(result) == 1
        assert result[0]["content"] == "user is Python dev"
        assert result[0]["memory_type"] == "identity"

    @pytest.mark.asyncio
    async def test_extract_json_in_markdown_code_block(self, make_fake_llm):
        """LLM wraps JSON in markdown code block → stripped and parsed."""
        make_fake_llm([
            [LLMResponse(content='```json\n[{"content":"fact 1","memory_type":"fact"}]\n```')],
        ])

        from agent.session_extract import _extract_with_llm
        result = await _extract_with_llm("[user] some conversation")

        assert len(result) == 1
        assert result[0]["content"] == "fact 1"

    @pytest.mark.asyncio
    async def test_extract_single_object_becomes_list(self, make_fake_llm):
        """LLM returns single object (not array) → wrapped in list."""
        make_fake_llm([
            [LLMResponse(content='{"content":"single fact","memory_type":"fact"}')],
        ])

        from agent.session_extract import _extract_with_llm
        result = await _extract_with_llm("[user] single message")

        assert isinstance(result, list)
        assert len(result) == 1
        assert result[0]["content"] == "single fact"

    @pytest.mark.asyncio
    async def test_extract_invalid_json_returns_none(self, make_fake_llm):
        """LLM returns garbage → None, so the caller can retry safely."""
        make_fake_llm([
            [LLMResponse(content="not valid json at all")],
        ])

        from agent.session_extract import _extract_with_llm
        result = await _extract_with_llm("[user] some text")

        assert result is None

    @pytest.mark.asyncio
    async def test_extract_empty_array(self, make_fake_llm):
        """LLM returns empty array."""
        make_fake_llm([
            [LLMResponse(content="[]")],
        ])

        from agent.session_extract import _extract_with_llm
        result = await _extract_with_llm("[user] no personal info")

        assert result == []


class TestExtractSessionMemories:
    @pytest.mark.asyncio
    async def test_two_message_conversation_is_processed(self):
        """A normal one-turn conversation must not be left pending forever."""
        from models.database import session_scope
        from models.orm import Conversation, Message

        conversation_id = str(uuid.uuid4())
        created_at = datetime.now(UTC)
        async with session_scope() as session:
            session.add(Conversation(id=conversation_id, title="one turn"))
            session.add_all([
                Message(
                    id=str(uuid.uuid4()), conversation_id=conversation_id,
                    role="user", content="我偏好简洁回答", created_at=created_at,
                ),
                Message(
                    id=str(uuid.uuid4()), conversation_id=conversation_id,
                    role="assistant", content="好的", created_at=created_at + timedelta(seconds=1),
                ),
            ])
            await session.commit()

        with patch(
            "agent.session_extract._extract_with_llm",
            new_callable=AsyncMock,
            return_value=[],
        ) as mock_extract:
            from agent.session_extract import extract_session_memories

            await extract_session_memories(conversation_id)

        mock_extract.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_no_messages_returns_early(self):
        """0 messages → early return."""
        from models.database import session_scope
        from models.orm import Conversation

        conversation_id = str(uuid.uuid4())
        async with session_scope() as session:
            session.add(Conversation(id=conversation_id, title="empty"))
            await session.commit()

        with patch(
            "agent.session_extract._extract_with_llm", new_callable=AsyncMock,
        ) as mock_extract:
            from agent.session_extract import extract_session_memories

            await extract_session_memories(conversation_id)

        mock_extract.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_success_advances_to_exact_message_watermark(self):
        from sqlalchemy import select

        from models.database import session_scope
        from models.orm import Conversation, Message

        conversation_id = str(uuid.uuid4())
        watermark = datetime.now(UTC).replace(microsecond=0)
        async with session_scope() as session:
            session.add(Conversation(id=conversation_id, title="watermark"))
            session.add(Message(
                id=str(uuid.uuid4()), conversation_id=conversation_id,
                role="user", content="我是 Python 开发者", created_at=watermark,
            ))
            await session.commit()

        extracted = [{"content": "Python 开发者", "memory_type": "identity_role"}]
        with (
            patch(
                "agent.session_extract._extract_with_llm",
                new_callable=AsyncMock,
                return_value=extracted,
            ),
            patch(
                "memory.profile.handle_session_extract",
                new_callable=AsyncMock,
                return_value={},
            ) as mock_handle,
        ):
            from agent.session_extract import extract_session_memories

            await extract_session_memories(conversation_id)

        async with session_scope() as session:
            stored = await session.scalar(select(Conversation).where(
                Conversation.id == conversation_id
            ))
        assert stored.last_extracted_at.replace(tzinfo=UTC) == watermark
        mock_handle.assert_awaited_once_with(extracted)

    @pytest.mark.asyncio
    async def test_parse_failure_does_not_advance_watermark(self):
        from sqlalchemy import select

        from models.database import session_scope
        from models.orm import Conversation, Message

        conversation_id = str(uuid.uuid4())
        async with session_scope() as session:
            session.add(Conversation(id=conversation_id, title="retry"))
            session.add(Message(
                id=str(uuid.uuid4()), conversation_id=conversation_id,
                role="user", content="需要重试", created_at=datetime.now(UTC),
            ))
            await session.commit()

        with patch(
            "agent.session_extract._extract_with_llm",
            new_callable=AsyncMock,
            return_value=None,
        ):
            from agent.session_extract import extract_session_memories

            await extract_session_memories(conversation_id)

        async with session_scope() as session:
            stored = await session.scalar(select(Conversation).where(
                Conversation.id == conversation_id
            ))
        assert stored.last_extracted_at is None
