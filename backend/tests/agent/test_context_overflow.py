"""Context overflow degradation path + _process_dropped tests."""

from unittest.mock import AsyncMock, patch

import pytest

from llm.base import LLMResponse, ToolCall
from tests.conftest import FakeLLM


class RaisingFakeLLM(FakeLLM):
    """FakeLLM that raises an exception on a specified call index."""

    def __init__(self, response_queues, raise_on_call=None):
        super().__init__(response_queues)
        self.raise_on_call = raise_on_call or {}

    async def chat_stream(self, messages=None, tools=None):
        if self.call_index in self.raise_on_call:
            idx = self.call_index
            self.call_index += 1
            raise self.raise_on_call[idx]
        async for resp in super().chat_stream(messages, tools):
            yield resp


def _make_tool_call(name, args, call_id="call_1"):
    return ToolCall(id=call_id, name=name, arguments=args)


def _inject_llm(llm_instance):
    import llm.factory
    llm.factory._llm_instance = llm_instance


def _events_by_type(events, event_type):
    return [e for e in events if e.get("event") == event_type]


class TestContextOverflowDegradation:
    """Tests for context overflow → window reduction → retry path in loop.py."""

    QUERY = "有哪些文档"  # matches document_listing rule, skips LLM classify

    @pytest.mark.asyncio
    async def test_overflow_triggers_window_reduction(self, make_fake_llm):
        """LLM raises context error at window=40000 → halves to 20000 → retry succeeds."""
        context_error = Exception("context_length_exceeded: maximum context length")

        fake = RaisingFakeLLM([
            # Main loop attempt 1: raises context error
            [LLMResponse(content="should not be seen")],
            # Main loop attempt 2: succeeds
            [LLMResponse(content="final answer")],
        ], raise_on_call={0: context_error})
        _inject_llm(fake)

        with patch("agent.loop.registry") as mock_registry:
            mock_registry.get_schemas.return_value = []
            mock_registry.execute = AsyncMock()

            with patch("agent.loop.get_window", return_value=40000):
                from agent.loop import run_agent_loop

                events = []
                async for event in run_agent_loop(self.QUERY, []):
                    events.append(event)

            status_msgs = [e["data"]["message"] for e in events if e["event"] == "status"]
            assert any("上下文" in m for m in status_msgs), f"status messages: {status_msgs}"
            chunks = _events_by_type(events, "answer_chunk")
            assert len(chunks) > 0
            done = _events_by_type(events, "done")
            assert len(done) == 1

    @pytest.mark.asyncio
    async def test_overflow_at_min_window_returns_error(self):
        """Window at 16000, context error → CONTEXT_ERROR returned."""
        import llm.factory
        llm.factory.reset_llm()

        context_error = Exception("context_length_exceeded: requested token count exceeds")
        fake = RaisingFakeLLM([
            [LLMResponse(content="should not be seen")],
        ], raise_on_call={0: context_error})
        _inject_llm(fake)

        with patch("agent.loop.registry") as mock_registry:
            mock_registry.get_schemas.return_value = []
            mock_registry.execute = AsyncMock()

            with patch("agent.loop.get_window", return_value=16000):
                from agent.loop import run_agent_loop

                events = []
                async for event in run_agent_loop(self.QUERY, []):
                    events.append(event)

            errors = _events_by_type(events, "error")
            assert len(errors) == 1
            assert errors[0]["data"]["code"] == "CONTEXT_ERROR"

    @pytest.mark.asyncio
    async def test_overflow_recovery_then_normal_completion(self, make_fake_llm):
        """Window halves once, second attempt succeeds → final answer + done."""
        context_error = Exception("maximum context length exceeded - reduce the length")

        fake = RaisingFakeLLM([
            [LLMResponse(content="first fail")],
            [LLMResponse(content="recovered result")],
        ], raise_on_call={0: context_error})
        _inject_llm(fake)

        with patch("agent.loop.registry") as mock_registry:
            mock_registry.get_schemas.return_value = []
            mock_registry.execute = AsyncMock()

            with patch("agent.loop.get_window", return_value=50000):
                from agent.loop import run_agent_loop

                events = []
                async for event in run_agent_loop(self.QUERY, []):
                    events.append(event)

            chunks = _events_by_type(events, "answer_chunk")
            done = _events_by_type(events, "done")
            assert len(chunks) > 0
            assert len(done) == 1

    @pytest.mark.asyncio
    async def test_non_context_error_not_swallowed(self):
        """ValueError (not context error) should propagate, not be caught by overflow handler."""
        import llm.factory
        llm.factory.reset_llm()

        fake = RaisingFakeLLM([
            [LLMResponse(content="will raise")],
        ], raise_on_call={0: ValueError("not a context error")})
        _inject_llm(fake)

        with patch("agent.loop.registry") as mock_registry:
            mock_registry.get_schemas.return_value = []
            mock_registry.execute = AsyncMock()

            from agent.loop import run_agent_loop

            with pytest.raises(ValueError, match="not a context error"):
                async for _event in run_agent_loop(self.QUERY, []):
                    pass

    def test_is_context_error_patterns(self):
        from agent.context_window import is_context_error

        assert is_context_error(Exception("context_length_exceeded: too many tokens")) is True
        assert is_context_error(Exception("Error: maximum context length 8192 exceeded")) is True
        assert is_context_error(Exception("please reduce the length of your input")) is True
        assert is_context_error(Exception("input too long for model")) is True
        assert is_context_error(Exception("requested token count exceeds model maximum")) is True
        assert is_context_error(Exception("normal error message")) is False


class TestProcessDropped:
    @pytest.mark.asyncio
    async def test_process_dropped_extracts_memories(self, make_fake_llm):
        """_process_dropped extracts memories from dropped queries → writes to profile."""
        # Queue 0: _extract_with_llm (LLM extract)
        # Queue 1: confirm_memory (single candidate confirmed via decide_memory tool)
        make_fake_llm([
            [LLMResponse(content='[{"content": "user is a tester", "memory_type": "identity"}]')],
            [LLMResponse(tool_calls=[
                _make_tool_call("decide_memory", {"save": True}, call_id="c1"),
            ])],
        ])

        with patch("memory.profile.handle_intercept") as mock_handle:
            mock_handle.return_value = {}
            from agent.loop import _process_dropped

            # Single query that doesn't match regex → goes through LLM → 1 candidate → single confirmation
            await _process_dropped(["I am a developer"])

            assert mock_handle.call_count >= 1

    @pytest.mark.asyncio
    async def test_process_dropped_empty_queries(self):
        """Empty query list → no extraction, no error."""
        from agent.loop import _process_dropped
        await _process_dropped([])

    @pytest.mark.asyncio
    async def test_process_dropped_exception_is_logged(self, make_fake_llm):
        """Internal exception in _process_dropped is caught and logged, not raised."""
        fake = RaisingFakeLLM([
            [LLMResponse(content="[]")],
        ], raise_on_call={0: RuntimeError("extraction failed")})
        _inject_llm(fake)

        with patch("agent.loop.logger") as mock_logger:
            from agent.loop import _process_dropped
            await _process_dropped(["test message"])
            mock_logger.warning.assert_called()
