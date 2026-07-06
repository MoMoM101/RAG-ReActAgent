"""Session-end memory extraction tests — _extract_with_llm and extract_session_memories."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from llm.base import LLMResponse


def _make_tool_call(name, args, call_id="call_1"):
    from llm.base import ToolCall
    return ToolCall(id=call_id, name=name, arguments=args)


class TestExtractWithLLM:
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
    async def test_extract_invalid_json_returns_empty(self, make_fake_llm):
        """LLM returns garbage → []."""
        make_fake_llm([
            [LLMResponse(content="not valid json at all")],
        ])

        from agent.session_extract import _extract_with_llm
        result = await _extract_with_llm("[user] some text")

        assert result == []

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
    async def test_skips_when_less_than_5_new_messages(self):
        """Less than 5 new messages → returns early, no LLM call."""
        mock_session = MagicMock()
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock()

        # First query: get last_extracted_at → None
        # Second query: count new messages → 3
        conv_result = MagicMock()
        conv_result.scalar_one_or_none.return_value = None
        count_result = MagicMock()
        count_result.scalar.return_value = 3

        mock_session.execute = AsyncMock(side_effect=[conv_result, count_result])

        with patch("models.database.async_session") as mock_factory:
            mock_factory.return_value = mock_session
            with patch("agent.session_extract._extract_with_llm") as mock_extract:
                from agent.session_extract import extract_session_memories
                await extract_session_memories("conv-123")

                mock_extract.assert_not_called()

    @pytest.mark.asyncio
    async def test_no_messages_returns_early(self):
        """0 messages → early return."""
        mock_session = MagicMock()
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock()

        conv_result = MagicMock()
        conv_result.scalar_one_or_none.return_value = None
        count_result = MagicMock()
        count_result.scalar.return_value = 0
        msg_result = MagicMock()
        msg_result.scalars.return_value.all.return_value = []

        mock_session.execute = AsyncMock(side_effect=[conv_result, count_result, msg_result])

        with patch("models.database.async_session") as mock_factory:
            mock_factory.return_value = mock_session
            with patch("agent.session_extract._extract_with_llm") as mock_extract:
                from agent.session_extract import extract_session_memories
                await extract_session_memories("conv-456")

                mock_extract.assert_not_called()
