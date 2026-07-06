"""Agent Loop 核心测试 — Mock LLM 注入，验证关键路径。"""

import pytest
from unittest.mock import AsyncMock, patch
from llm.base import LLMResponse, ToolCall, ChatMessage
from agent.tools import ToolResult


def _make_tool_call(name: str, args: dict, call_id: str = "call_1") -> ToolCall:
    return ToolCall(id=call_id, name=name, arguments=args)


def _events_by_type(events: list[dict], event_type: str) -> list[dict]:
    return [e for e in events if e.get("event") == event_type]


class TestAgentLoopBasic:
    @pytest.mark.asyncio
    async def test_direct_answer_no_tools(self, make_fake_llm):
        """规则命中 + LLM 直接回答（无 tool_call）→ answer_chunk + done。"""
        make_fake_llm([
            [LLMResponse(content="你好！有什么可以帮你的？")],
        ])

        with patch("agent.loop.registry") as mock_registry:
            mock_registry.get_schemas.return_value = []
            mock_registry.execute = AsyncMock()

            from agent.loop import run_agent_loop

            history = [ChatMessage(role="user", content="你好"), ChatMessage(role="assistant", content="你好")]
            events = []
            async for event in run_agent_loop("好的", history):
                events.append(event)

            chunks = _events_by_type(events, "answer_chunk")
            done = _events_by_type(events, "done")
            assert len(chunks) > 0
            assert len(done) == 1
            mock_registry.execute.assert_not_called()

    @pytest.mark.asyncio
    async def test_single_tool_call(self, make_fake_llm):
        """规则命中 → LLM 调 search_docs → tool_result + answer_chunk + done。"""
        make_fake_llm([
            # 主循环第 1 轮：调 search_docs
            [
                LLMResponse(
                    tool_calls=[_make_tool_call("search_docs", {"query": "测试检索"})],
                ),
            ],
            # 主循环第 2 轮：返回最终回答
            [LLMResponse(content="根据检索结果，这是回答内容。")],
        ])

        with patch("agent.loop.registry") as mock_registry:
            mock_registry.get_schemas.return_value = []
            mock_registry.execute = AsyncMock(return_value=ToolResult(
                success=True,
                data={"results": [{"document_id": "d1", "filename": "test.txt", "text": "测试内容", "score": 0.9}], "count": 1},
            ))

            from agent.loop import run_agent_loop

            events = []
            async for event in run_agent_loop("有哪些文档", []):
                events.append(event)

            tool_calls = _events_by_type(events, "tool_call")
            tool_results = _events_by_type(events, "tool_result")
            chunks = _events_by_type(events, "answer_chunk")
            done = _events_by_type(events, "done")

            assert len(tool_calls) == 1
            assert tool_calls[0]["data"]["tool"] == "search_docs"
            assert len(tool_results) == 1
            assert tool_results[0]["data"]["success"] is True
            assert len(chunks) > 0
            assert len(done) == 1


class TestAgentLoopToolError:
    @pytest.mark.asyncio
    async def test_tool_execution_failure(self, make_fake_llm):
        """工具执行失败 → tool_result 含 error。"""
        make_fake_llm([
            [
                LLMResponse(
                    tool_calls=[_make_tool_call("calculator", {"expression": "1/0"})],
                ),
            ],
            [LLMResponse(content="计算出错了")],
        ])

        with patch("agent.loop.registry") as mock_registry:
            mock_registry.get_schemas.return_value = []
            mock_registry.execute = AsyncMock(return_value=ToolResult(
                success=False, error="division by zero",
            ))

            from agent.loop import run_agent_loop

            events = []
            async for event in run_agent_loop("帮我算一下3*4", []):
                events.append(event)

            tool_results = _events_by_type(events, "tool_result")
            assert len(tool_results) >= 1
            assert tool_results[0]["data"]["success"] is False
            assert "division by zero" in tool_results[0]["data"]["error"]


class TestAgentLoopSources:
    @pytest.mark.asyncio
    async def test_source_extraction(self, make_fake_llm):
        """search_docs 结果 → sources 事件包含文档信息。"""
        make_fake_llm([
            [
                LLMResponse(
                    tool_calls=[_make_tool_call("search_docs", {"query": "X"})],
                ),
            ],
            [LLMResponse(content="检索结果如上")],
        ])

        with patch("agent.loop.registry") as mock_registry:
            mock_registry.get_schemas.return_value = []
            mock_registry.execute = AsyncMock(return_value=ToolResult(
                success=True,
                data={
                    "results": [
                        {"document_id": "abc12345", "filename": "readme.txt", "text": "重要内容", "score": 0.92},
                    ],
                    "count": 1,
                },
            ))

            from agent.loop import run_agent_loop

            events = []
            async for event in run_agent_loop("有哪些文档", []):
                events.append(event)

            sources = _events_by_type(events, "sources")
            assert len(sources) == 1
            assert sources[0]["data"][0]["filename"] == "readme.txt"
            assert sources[0]["data"][0]["rank"] == 1


class TestAgentLoopLimits:
    @pytest.mark.asyncio
    async def test_loop_limit(self, make_fake_llm):
        """主循环始终返回 tool_call → 达到 max_loop_iterations 后 LOOP_LIMIT 错误。"""
        from config import settings

        # 构造超过 max_loop_iterations 轮的工具调用响应
        max_iter = settings.max_loop_iterations
        queues = []
        for _ in range(max_iter):
            queues.append([
                LLMResponse(
                    tool_calls=[_make_tool_call("calculator", {"expression": "1+1"})],
                ),
            ])
        queues.append([LLMResponse(content="final")])

        make_fake_llm(queues)

        with patch("agent.loop.registry") as mock_registry:
            mock_registry.get_schemas.return_value = []
            mock_registry.execute = AsyncMock(return_value=ToolResult(
                success=True, data={"result": 2},
            ))

            from agent.loop import run_agent_loop

            events = []
            async for event in run_agent_loop("帮我算一下3*4", []):
                events.append(event)

            errors = [e for e in events if e.get("event") == "error"]
            assert len(errors) >= 1
            assert any(e["data"]["code"] == "LOOP_LIMIT" for e in errors)


class TestAgentLoopParallelTools:
    @pytest.mark.asyncio
    async def test_parallel_tool_calls(self, make_fake_llm):
        """LLM returns multiple tool_calls → all executed, all results reported."""
        make_fake_llm([
            # Intent classification (needed because query doesn't match rules)
            [LLMResponse(tool_calls=[_make_tool_call("classify_intent", {
                "intent": "knowledge_retrieval",
                "suggested_tools": ["search_docs"],
                "hint_text": "",
            }, call_id="ic")])],
            # Round 1: 2 parallel tool_calls
            [
                LLMResponse(
                    tool_calls=[
                        _make_tool_call("search_docs", {"query": "X"}, call_id="c1"),
                        _make_tool_call("recall_memory", {"query": "Y"}, call_id="c2"),
                    ],
                ),
            ],
            # Round 2: final answer
            [LLMResponse(content="combined result")],
        ])

        with patch("agent.loop.registry") as mock_registry:
            mock_registry.get_schemas.return_value = []
            mock_registry.execute = AsyncMock(return_value=ToolResult(
                success=True, data={"results": [], "count": 0},
            ))

            from agent.loop import run_agent_loop

            events = []
            async for event in run_agent_loop("search X and recall Y", []):
                events.append(event)

            tool_calls = _events_by_type(events, "tool_call")
            tool_results = _events_by_type(events, "tool_result")

            assert len(tool_calls) == 2
            assert {tc["data"]["tool"] for tc in tool_calls} == {"search_docs", "recall_memory"}
            assert len(tool_results) == 2
            assert all(tr["data"]["success"] for tr in tool_results)

    @pytest.mark.asyncio
    async def test_parallel_one_fails_one_succeeds(self, make_fake_llm):
        """One tool_call succeeds, one fails → both results reported."""
        make_fake_llm([
            # Intent classification
            [LLMResponse(tool_calls=[_make_tool_call("classify_intent", {
                "intent": "knowledge_retrieval",
                "suggested_tools": ["search_docs", "calculator"],
                "hint_text": "",
            }, call_id="ic")])],
            # Round 1: 2 parallel tool_calls
            [
                LLMResponse(
                    tool_calls=[
                        _make_tool_call("search_docs", {"query": "X"}, call_id="c1"),
                        _make_tool_call("calculator", {"expression": "1/0"}, call_id="c2"),
                    ],
                ),
            ],
            # Round 2: final answer
            [LLMResponse(content="partial result")],
        ])

        # search_docs succeeds, calculator fails
        async def side_effect(name, **kw):
            if name == "calculator":
                return ToolResult(success=False, error="division by zero")
            return ToolResult(success=True, data={"results": [], "count": 0})

        with patch("agent.loop.registry") as mock_registry:
            mock_registry.get_schemas.return_value = []
            mock_registry.execute = AsyncMock(side_effect=side_effect)

            from agent.loop import run_agent_loop

            events = []
            async for event in run_agent_loop("calc and search", []):
                events.append(event)

            tool_results = _events_by_type(events, "tool_result")
            assert len(tool_results) == 2
            successes = [tr["data"]["success"] for tr in tool_results]
            assert True in successes
            assert False in successes
