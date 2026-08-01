"""Agent Loop 核心测试 — Mock LLM 注入，验证关键路径。"""

import asyncio
import json
from unittest.mock import AsyncMock, patch

import pytest

from agent.loop import _tools_during_forced_convergence
from agent.loop_support import detect_repetitive_tool_calls
from agent.loop_tools import ToolTurnState, _prune_sources, _register_search_sources
from agent.source_utils import extract_sources
from agent.tools import ToolResult
from llm.base import ChatMessage, LLMResponse, ToolCall


def _make_tool_call(name: str, args: dict, call_id: str = "call_1") -> ToolCall:
    return ToolCall(id=call_id, name=name, arguments=args)


def _make_classifier_queue(intent="general_chat", suggested_tools=None, hint_text="search"):
    """Return a FakeLLM queue entry for the intent classifier LLM call."""
    if suggested_tools is None:
        suggested_tools = ["search_docs"]
    return [LLMResponse(tool_calls=[
        _make_tool_call("classify_intent", {
            "intent": intent,
            "suggested_tools": suggested_tools,
            "hint_text": hint_text,
        }, call_id="ci"),
    ])]


def _events_by_type(events: list[dict], event_type: str) -> list[dict]:
    return [e for e in events if e.get("event") == event_type]


def _retrieval_payload(message: ChatMessage) -> dict:
    serialized = next(
        line
        for line in (message.content or "").splitlines()
        if line.startswith('{"retrieval_groups"')
    )
    return json.loads(serialized)


def _make_parallel_result(name: str, result: ToolResult, elapsed: float = 0.0):
    """Return a single-element execute_parallel result list."""
    return [(name, result, elapsed)]


def test_calculator_call_requires_real_arithmetic_expression():
    from agent.loop import (
        _calculator_call_uses_known_values,
        _filter_calculator_calls,
        _meaningful_calculator_call,
        _required_calculator_result_count,
        _runtime_trusted_calculator_results,
    )

    assert not _meaningful_calculator_call(
        _make_tool_call("calculator", {"expression": "7600"}),
    )
    assert _meaningful_calculator_call(
        _make_tool_call("calculator", {"expression": "7600 + 8000"}),
    )
    assert _meaningful_calculator_call(
        _make_tool_call("search_docs", {"query": "7600"}),
    )
    assert _calculator_call_uses_known_values(
        _make_tool_call("calculator", {"expression": "30480 * (1 - 0.1)"}),
        {30480.0, 0.1},
    )
    assert not _calculator_call_uses_known_values(
        _make_tool_call("calculator", {"expression": "34080 * (1 - 0.1)"}),
        {30480.0, 0.1},
    )
    batch = [
        _make_tool_call("calculator", {"expression": "1280 * 12"}, "c1"),
        _make_tool_call("calculator", {"expression": "(75 - 40) * 36 * 12"}, "c2"),
        _make_tool_call("calculator", {"expression": "15360 + 15120"}, "c3"),
        _make_tool_call("calculator", {"expression": "34080 * (1 - 0.1)"}, "c4"),
    ]
    accepted = _filter_calculator_calls(
        batch,
        {1280.0, 12.0, 75.0, 40.0, 36.0, 0.1},
        enforce_provenance=True,
    )
    assert [call.id for call in accepted] == ["c1", "c2", "c3"]

    duplicates = _filter_calculator_calls(
        [
            _make_tool_call("calculator", {"expression": "1280 * 12"}, "d1"),
            _make_tool_call("calculator", {"expression": "1280*12"}, "d2"),
        ],
        {1280.0, 12.0},
        enforce_provenance=True,
    )
    assert [call.id for call in duplicates] == ["d1"]
    assert _filter_calculator_calls(
        [_make_tool_call("calculator", {"expression": "1280 * 12"}, "d3")],
        {1280.0, 12.0, 15360.0},
        enforce_provenance=True,
        trusted_results={15360.0},
    ) == []

    compact_budget = _filter_calculator_calls(
        [
            _make_tool_call("calculator", {"expression": "75 - 40"}, "h1"),
            _make_tool_call("calculator", {"expression": "1 - 0.1"}, "h2"),
            _make_tool_call(
                "calculator",
                {"expression": "(1280 + (75 - 40) * 36) * 12"},
                "target1",
            ),
            _make_tool_call(
                "calculator",
                {"expression": "30480 * (1 - 0.1)"},
                "target2",
            ),
            _make_tool_call(
                "calculator",
                {"expression": "7600 + 27432 + 8000"},
                "target3",
            ),
        ],
        {1280.0, 75.0, 40.0, 36.0, 12.0, 30480.0, 0.1, 7600.0, 27432.0, 8000.0},
        enforce_provenance=True,
        min_operations=2,
        max_new_results=3,
    )
    assert [call.id for call in compact_budget] == ["target1", "target2", "target3"]

    assert _required_calculator_result_count("计算总额") == 1
    assert _required_calculator_result_count("计算折后总额") == 2
    assert _required_calculator_result_count("列出订阅原价、年付折扣和最终总额") == 3
    assert _required_calculator_result_count(
        "75个节点，列出订阅原价、年付折扣和最终总额"
    ) == 3

    failed_predecessor = {
        "c1": {"success": False, "full_data": None},
        "c2": {"success": True, "full_data": {"result": 23360}},
    }
    dependent_batch = [
        _make_tool_call("calculator", {"expression": "1280 * 12"}, "c1"),
        _make_tool_call("calculator", {"expression": "15360 + 8000"}, "c2"),
    ]
    assert _runtime_trusted_calculator_results(
        dependent_batch,
        failed_predecessor,
        {1280.0, 12.0, 8000.0},
    ) == set()

    successful_chain = {
        "c1": {"success": True, "full_data": {"result": 15360}},
        "c2": {"success": True, "full_data": {"result": 23360}},
    }
    assert _runtime_trusted_calculator_results(
        dependent_batch,
        successful_chain,
        {1280.0, 12.0, 8000.0},
    ) == {15360.0, 23360.0}


class TestAgentLoopBasic:
    @pytest.mark.asyncio
    async def test_direct_answer_no_tools(self, make_fake_llm):
        """v0.2.0: all queries go through LLM classifier, then direct answer → answer_chunk + done."""
        make_fake_llm(
            [
                _make_classifier_queue(intent="general_chat"),
                [LLMResponse(content="你好！有什么可以帮你的？")],
            ]
        )

        with patch("agent.loop.registry") as mock_registry:
            mock_registry.get_schemas.return_value = []
            mock_registry.execute_parallel = AsyncMock()

            from agent.loop import run_agent_loop

            history = [ChatMessage(role="user", content="你好"), ChatMessage(role="assistant", content="你好")]
            events = []
            async for event in run_agent_loop("好的", history):
                events.append(event)

            chunks = _events_by_type(events, "answer_chunk")
            done = _events_by_type(events, "done")
            assert len(chunks) > 0
            assert len(done) == 1
            mock_registry.execute_parallel.assert_not_called()

    @pytest.mark.asyncio
    async def test_single_tool_call(self, make_fake_llm):
        """v0.2.0: classifier → LLM calls search_docs → tool_result + answer_chunk + done."""
        make_fake_llm(
            [
                _make_classifier_queue(),
                # 主循环第 1 轮：调 search_docs
                [
                    LLMResponse(
                        tool_calls=[_make_tool_call("search_docs", {"query": "测试检索"})],
                    ),
                ],
                # 主循环第 2 轮：返回最终回答
                [LLMResponse(content="测试内容 [S1]。")],
            ]
        )

        with patch("agent.loop.registry") as mock_registry:
            mock_registry.get_schemas.return_value = []
            mock_registry.execute_parallel = AsyncMock(
                return_value=_make_parallel_result(
                    "search_docs",
                    ToolResult(
                        success=True,
                        data={
                            "results": [{"document_id": "d1", "filename": "test.txt", "text": "测试内容", "score": 0.9}],
                            "count": 1,
                        },
                    ),
                )
            )

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

    @pytest.mark.asyncio
    async def test_budget_query_forces_calculator_after_retrieval(self, make_fake_llm):
        make_fake_llm(
            [
                _make_classifier_queue(),
                [LLMResponse(tool_calls=[_make_tool_call("search_docs", {"query": "价格"}, "search-1")])],
                [LLMResponse(content="设备单价为 7600 元 [S1]，总预算为 15600 元。")],
                [LLMResponse(tool_calls=[_make_tool_call("calculator", {"expression": "7600+8000"}, "calc-1")])],
                [LLMResponse(content="设备单价为 7600 元 [S1]。总预算 = 7600 + 8000 = 15600 元。")],
                _make_classifier_queue(),
                [LLMResponse(tool_calls=[_make_tool_call("search_docs", {"query": "价格"}, "search-2")])],
                [LLMResponse(content="设备单价为 7600 元 [S1]，总预算为 15600 元。")],
                [LLMResponse(tool_calls=[_make_tool_call("calculator", {"expression": "7600+8000"}, "calc-2")])],
                [LLMResponse(content="设备单价为 7600 元 [S1]。总预算 = 7600 + 8000 = 15600 元。")],
            ]
        )

        search_result = ToolResult(
            success=True,
            data={
                "results": [
                    {
                        "chunk_id": "price-1",
                        "document_id": "d1",
                        "filename": "pricing.xlsx",
                        "text": "设备单价为 7600 元。",
                        "score": 0.9,
                    }
                ],
                "count": 1,
            },
        )
        calculator_result = ToolResult(
            success=True,
            data={"expression": "7600+8000", "result": 15600},
        )

        with patch("agent.loop.registry") as mock_registry:
            mock_registry.get_schemas.return_value = []
            mock_registry.execute_parallel = AsyncMock(
                side_effect=[
                    _make_parallel_result("search_docs", search_result),
                    _make_parallel_result("calculator", calculator_result),
                    _make_parallel_result("search_docs", search_result),
                    _make_parallel_result("calculator", calculator_result),
                ]
            )

            from agent.loop import run_agent_loop
            from rag.answer_cache import get_answer_cache

            get_answer_cache().clear()

            events = [
                event
                async for event in run_agent_loop(
                    "请计算 1 台设备加 8000 元实施费的总预算",
                    [],
                )
            ]
            repeated_events = [
                event
                async for event in run_agent_loop(
                    "请计算 1 台设备加 8000 元实施费的总预算",
                    [],
                )
            ]

        called_tools = [event["data"]["tool"] for event in _events_by_type(events, "tool_call")]
        assert called_tools == ["search_docs", "calculator"]
        repeated_tools = [
            event["data"]["tool"]
            for event in _events_by_type(repeated_events, "tool_call")
        ]
        assert repeated_tools == ["search_docs", "calculator"]
        assert not any(
            event["data"].get("cache_hit")
            for event in _events_by_type(repeated_events, "timing")
        )
        assert "15600" in "".join(event["data"]["delta"] for event in _events_by_type(events, "answer_chunk"))

    @pytest.mark.asyncio
    async def test_budget_draft_equation_is_recovered_as_real_calculator_call(
        self,
        make_fake_llm,
    ):
        make_fake_llm(
            [
                _make_classifier_queue(),
                [LLMResponse(tool_calls=[
                    _make_tool_call("search_docs", {"query": "price"}, "search"),
                ])],
                [LLMResponse(content="总预算 = 7600 + 8000 = 15600 元。")],
                [LLMResponse(content="设备7600元 [S1]。总预算 = 7600 + 8000 = 15600元。")],
            ]
        )
        source = ToolResult(
            success=True,
            data={
                "results": [{
                    "chunk_id": "price-1",
                    "document_id": "d1",
                    "filename": "pricing.xlsx",
                    "text": "设备价格7600元，实施服务8000元。",
                    "score": 0.9,
                }],
                "count": 1,
            },
        )
        calculation = ToolResult(
            success=True,
            data={"expression": "7600+8000", "result": 15600},
        )

        with patch("agent.loop.registry") as mock_registry:
            mock_registry.get_schemas.return_value = []
            mock_registry.execute_parallel = AsyncMock(
                side_effect=[
                    _make_parallel_result("search_docs", source),
                    _make_parallel_result("calculator", calculation),
                ],
            )
            from agent.loop import run_agent_loop

            events = [
                event
                async for event in run_agent_loop(
                    "请计算7600元设备和8000元实施服务的总预算",
                    [],
                )
            ]

        calls = _events_by_type(events, "tool_call")
        assert [event["data"]["tool"] for event in calls] == ["search_docs", "calculator"]
        assert calls[1]["data"]["args"]["expression"] == "7600+8000"
        assert any(
            event.get("event") == "status"
            and event["data"].get("code") == "CALCULATOR_RECOVERED"
            for event in events
        )
        assert "15600" in "".join(
            event["data"]["delta"]
            for event in _events_by_type(events, "answer_chunk")
        )

    @pytest.mark.asyncio
    async def test_rejected_calculator_batch_is_replanned_instead_of_returning_empty_answer(
        self,
        make_fake_llm,
    ):
        make_fake_llm(
            [
                _make_classifier_queue(),
                [LLMResponse(tool_calls=[_make_tool_call("search_docs", {"query": "价格"}, "s1")])],
                [LLMResponse(tool_calls=[_make_tool_call("calculator", {"expression": "34080*0.9"}, "bad")])],
                [LLMResponse(tool_calls=[_make_tool_call("calculator", {"expression": "7600+8000"}, "good")])],
                [LLMResponse(content="总预算 = 7600 + 8000 = 15600 元。")],
            ]
        )
        search_result = ToolResult(
            success=True,
            data={
                "results": [{
                    "chunk_id": "price-1",
                    "document_id": "d1",
                    "filename": "pricing.xlsx",
                    "text": "设备单价为7600元。",
                    "score": 0.9,
                }],
                "count": 1,
            },
        )
        calculator_result = ToolResult(
            success=True,
            data={"expression": "7600+8000", "result": 15600},
        )

        with patch("agent.loop.registry") as mock_registry:
            mock_registry.get_schemas.return_value = []
            mock_registry.execute_parallel = AsyncMock(
                side_effect=[
                    _make_parallel_result("search_docs", search_result),
                    _make_parallel_result("calculator", calculator_result),
                ],
            )
            from agent.loop import run_agent_loop

            events = [
                event
                async for event in run_agent_loop(
                    "请计算1台设备加8000元实施费的总预算",
                    [],
                )
            ]

        called_tools = [event["data"]["tool"] for event in _events_by_type(events, "tool_call")]
        assert called_tools == ["search_docs", "calculator"]
        answer = "".join(event["data"]["delta"] for event in _events_by_type(events, "answer_chunk"))
        assert "15600" in answer
        assert "未能生成完整" not in answer


    @pytest.mark.asyncio
    async def test_complex_budget_rejects_duplicate_steps_and_requires_full_chain(
        self,
        make_fake_llm,
        monkeypatch,
    ):
        from config import settings

        monkeypatch.setattr(settings, "grounding_stream_verify_enabled", True)
        make_fake_llm(
            [
                _make_classifier_queue(),
                [LLMResponse(tool_calls=[_make_tool_call("search_docs", {"query": "pricing"}, "s1")])],
                [LLMResponse(tool_calls=[
                    _make_tool_call("calculator", {"expression": "1280*12"}, "dup1"),
                    _make_tool_call("calculator", {"expression": "1280 * 12"}, "dup2"),
                    _make_tool_call("calculator", {"expression": "1280*12"}, "dup3"),
                ])],
                [LLMResponse(tool_calls=[
                    _make_tool_call(
                        "calculator",
                        {"expression": "(1280+(75-40)*36)*12"},
                        "target-original",
                    ),
                    _make_tool_call(
                        "calculator",
                        {"expression": "30480*(1-0.1)"},
                        "target-discounted",
                    ),
                    _make_tool_call(
                        "calculator",
                        {"expression": "7600+27432+8000"},
                        "target-total",
                    ),
                ])],
                [LLMResponse(content=(
                    "硬件价格为7600元，实施费为8000元 [S1]。"
                    "订阅原价为30480元。年付折后订阅为27432元。最终总额为43032元。"
                ))],
            ]
        )
        source = ToolResult(
            success=True,
            data={
                "results": [{
                    "chunk_id": "price-1",
                    "document_id": "d1",
                    "filename": "pricing.xlsx",
                    "text": (
                        "XG-7 Pro硬件价格为7600元。平台基础订阅为每月1280元，"
                        "包含40个节点；超额节点价格为每节点每月36元。"
                        "一年按照12个月计费，年付折扣为0.1，标准实施服务费为8000元。"
                    ),
                    "score": 0.9,
                }],
                "count": 1,
            },
        )
        remaining_calculations = [
            ("calculator", ToolResult(success=True, data={"expression": "(1280+(75-40)*36)*12", "result": 30480}), 0.0),
            ("calculator", ToolResult(success=True, data={"expression": "30480*(1-0.1)", "result": 27432}), 0.0),
            ("calculator", ToolResult(success=True, data={"expression": "7600+27432+8000", "result": 43032}), 0.0),
        ]

        with patch("agent.loop.registry") as mock_registry:
            mock_registry.get_schemas.return_value = []
            mock_registry.execute_parallel = AsyncMock(
                side_effect=[
                    _make_parallel_result("search_docs", source),
                    remaining_calculations,
                ],
            )
            from agent.loop import run_agent_loop

            events = [
                event
                async for event in run_agent_loop(
                    "1台XG-7 Pro、75个节点、1年订阅，请列出订阅原价、年付折扣和最终总额。",
                    [],
                )
            ]

        calculator_calls = [
            event for event in _events_by_type(events, "tool_call")
            if event["data"]["tool"] == "calculator"
        ]
        assert len(calculator_calls) == 3
        assert len({event["data"]["args"]["expression"].replace(" ", "") for event in calculator_calls}) == 3
        answer = "".join(
            event["data"]["delta"] for event in _events_by_type(events, "answer_chunk")
        )
        assert "43032" in answer, _events_by_type(events, "timing")
        assert "无法确认" not in answer

    @pytest.mark.asyncio
    async def test_failed_calculator_does_not_satisfy_budget_enforcement(
        self,
        make_fake_llm,
        monkeypatch,
    ):
        from config import settings

        monkeypatch.setattr(settings, "grounding_stream_verify_enabled", False)
        make_fake_llm(
            [
                _make_classifier_queue(),
                [LLMResponse(tool_calls=[_make_tool_call("search_docs", {"query": "price"}, "s1")])],
                [LLMResponse(tool_calls=[_make_tool_call("calculator", {"expression": "7600/0"}, "bad")])],
                [LLMResponse(content="The total budget is 15600 yuan [S1].")],
                [LLMResponse(tool_calls=[_make_tool_call("calculator", {"expression": "7600+8000"}, "good")])],
                [LLMResponse(content="The total budget is 7600 + 8000 = 15600 yuan [S1].")],
            ]
        )
        search_result = ToolResult(
            success=True,
            data={
                "results": [{
                    "chunk_id": "price-1",
                    "document_id": "d1",
                    "filename": "pricing.xlsx",
                    "text": "The device costs 7600 yuan.",
                    "score": 0.9,
                }],
                "count": 1,
            },
        )
        failed_result = ToolResult(success=False, error="division by zero")
        successful_result = ToolResult(
            success=True,
            data={"expression": "7600+8000", "result": 15600},
        )

        with patch("agent.loop.registry") as mock_registry:
            mock_registry.get_schemas.return_value = []
            mock_registry.execute_parallel = AsyncMock(
                side_effect=[
                    _make_parallel_result("search_docs", search_result),
                    _make_parallel_result("calculator", failed_result),
                    _make_parallel_result("calculator", successful_result),
                ],
            )
            from agent.loop import run_agent_loop

            events = [
                event
                async for event in run_agent_loop(
                    "请计算1台设备加8000元实施费的总预算，除数0仅用于异常测试。",
                    [],
                )
            ]

        tool_results = _events_by_type(events, "tool_result")
        assert [event["data"]["tool"] for event in tool_results] == [
            "search_docs",
            "calculator",
            "calculator",
        ]
        assert tool_results[1]["data"]["success"] is False
        assert tool_results[2]["data"]["success"] is True
        answer = "".join(
            event["data"]["delta"] for event in _events_by_type(events, "answer_chunk")
        )
        assert "15600" in answer


class TestAgentLoopToolError:
    @pytest.mark.asyncio
    async def test_tool_execution_failure(self, make_fake_llm):
        """v0.2.0: classifier → LLM calls calculator → tool_result with error."""
        make_fake_llm(
            [
                _make_classifier_queue(),
                [
                    LLMResponse(
                        tool_calls=[_make_tool_call("calculator", {"expression": "1/0"})],
                    ),
                ],
                [LLMResponse(content="计算出错了")],
            ]
        )

        with patch("agent.loop.registry") as mock_registry:
            mock_registry.get_schemas.return_value = []
            mock_registry.execute_parallel = AsyncMock(
                return_value=_make_parallel_result(
                    "calculator",
                    ToolResult(success=False, error="division by zero"),
                )
            )

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
    async def test_tool_call_is_streamed_before_blocked_execution_finishes(
        self,
        make_fake_llm,
    ):
        make_fake_llm(
            [
                _make_classifier_queue(),
                [
                    LLMResponse(
                        tool_calls=[
                            _make_tool_call("search_docs", {"query": "产品规格"}),
                        ],
                    )
                ],
                [LLMResponse(content="已完成检索。")],
            ]
        )
        execution_started = asyncio.Event()
        release_execution = asyncio.Event()
        result = ToolResult(
            success=True,
            data={"count": 0, "results": []},
        )

        async def delayed_execute(calls, *, on_result=None):
            execution_started.set()
            await release_execution.wait()
            if on_result is not None:
                await on_result(0, calls[0]["name"], result, 25.0)
            return [(calls[0]["name"], result, 25.0)]

        with patch("agent.loop.registry") as mock_registry:
            mock_registry.get_schemas.return_value = []
            mock_registry.execute_parallel = AsyncMock(side_effect=delayed_execute)

            from agent.loop import run_agent_loop

            stream = run_agent_loop("列出产品规格", [])
            first_tool_event = None
            while first_tool_event is None:
                event = await asyncio.wait_for(anext(stream), timeout=2)
                if event["event"] == "tool_call":
                    first_tool_event = event

            assert first_tool_event["data"]["tool"] == "search_docs"
            assert execution_started.is_set()
            assert not release_execution.is_set()

            release_execution.set()
            remaining = [event async for event in stream]

        assert any(event["event"] == "tool_result" for event in remaining)

    @pytest.mark.asyncio
    async def test_parallel_tool_results_stream_in_completion_order(
        self,
        make_fake_llm,
    ):
        make_fake_llm(
            [
                _make_classifier_queue(),
                [
                    LLMResponse(
                        tool_calls=[
                            _make_tool_call("calculator", {"expression": "1+1"}, "calc-1"),
                            _make_tool_call("calculator", {"expression": "2+2"}, "calc-2"),
                        ],
                    )
                ],
                [LLMResponse(content="计算完成。")],
            ]
        )
        release_slower_result = asyncio.Event()
        first = ToolResult(success=True, data={"result": 2})
        second = ToolResult(success=True, data={"result": 4})

        async def completion_order_execute(calls, *, on_result=None):
            assert on_result is not None
            await on_result(1, calls[1]["name"], second, 5.0)
            await release_slower_result.wait()
            await on_result(0, calls[0]["name"], first, 20.0)
            return [
                (calls[0]["name"], first, 20.0),
                (calls[1]["name"], second, 5.0),
            ]

        with patch("agent.loop.registry") as mock_registry:
            mock_registry.get_schemas.return_value = []
            mock_registry.execute_parallel = AsyncMock(
                side_effect=completion_order_execute,
            )

            from agent.loop import run_agent_loop

            stream = run_agent_loop("计算两个表达式", [])
            first_result = None
            while first_result is None:
                event = await asyncio.wait_for(anext(stream), timeout=2)
                if event["event"] == "tool_result":
                    first_result = event

            assert first_result["data"]["result_value"] == 4
            assert first_result["data"]["call_id"] == "calc-2"
            assert not release_slower_result.is_set()

            release_slower_result.set()
            remaining = [event async for event in stream]

        result_values = [
            event["data"]["result_value"]
            for event in remaining
            if event["event"] == "tool_result"
        ]
        assert result_values == [2]
        assert next(
            event["data"]["call_id"]
            for event in remaining
            if event["event"] == "tool_result"
        ) == "calc-1"

    def test_document_list_is_registered_as_citeable_evidence(self):
        data = {
            "count": 2,
            "documents": [
                {
                    "id": "doc-1",
                    "filename": "01_product_guide.md",
                    "file_type": ".md",
                    "status": "ready",
                },
                {
                    "id": "doc-2",
                    "filename": "02_pricing.xlsx",
                    "file_type": ".xlsx",
                    "status": "ready",
                },
            ],
        }
        state = ToolTurnState(
            messages=[],
            sources=[],
            citation_by_source={},
            search_groups_by_source={},
            timing={},
        )

        _register_search_sources(
            state,
            "list_documents",
            ToolResult(success=True, data=data),
            "list-call",
        )

        assert data["citation_id"] == "S1"
        assert state.sources[0]["filename"] == "知识库文档列表"
        assert "当前知识库共有 2 份文档" in state.sources[0]["text"]
        assert "02_pricing.xlsx" in state.sources[0]["text"]
        assert state.sources[0]["documents"] == [
            {
                "filename": "01_product_guide.md",
                "file_type": ".md",
                "status": "ready",
            },
            {
                "filename": "02_pricing.xlsx",
                "file_type": ".xlsx",
                "status": "ready",
            },
        ]

    def test_document_list_source_survives_many_search_groups(self):
        inventory = {
            "citation_id": "S1",
            "chunk_id": "tool:list_documents",
            "document_id": "",
            "document_key": "tool:list_documents",
            "section_key": "当前文档清单",
            "filename": "知识库文档列表",
            "text": "当前知识库共有 9 份文档。",
            "source_type": "tool",
            "score": 1.0,
            "rank": 1,
        }
        searches = [
            {
                "citation_id": f"S{index + 2}",
                "chunk_id": f"chunk-{index}",
                "document_id": f"doc-{index}",
                "document_key": f"doc-{index}",
                "section_key": f"section-{index}",
                "text": f"topic evidence {index}",
                "score": 1.0,
                "rank": index + 2,
            }
            for index in range(15)
        ]
        sources = [inventory, *searches]
        state = ToolTurnState(
            messages=[ChatMessage(role="tool", content="{}", tool_name="search_docs")],
            sources=sources,
            citation_by_source={source["chunk_id"]: source["citation_id"] for source in sources},
            search_groups_by_source={
                inventory["chunk_id"]: {"list"},
                **{source["chunk_id"]: {f"search-{index}"} for index, source in enumerate(searches)},
            },
            timing={},
        )

        _prune_sources(state)

        assert len(state.sources) == 16
        assert state.sources[0]["chunk_id"] == "tool:list_documents"
        assert any(source["citation_id"] == "S1" for source in extract_sources(state.messages))
        payload = _retrieval_payload(state.messages[-1])
        assert len(payload["retrieval_groups"]) == 15
        assert all(group["source_ids"] for group in payload["retrieval_groups"])

    def test_multi_topic_pruning_reserves_distinct_documents(self):
        documents = ["product", "pricing", "operations", "sla"]
        group_by_document = {
            "product": "spec",
            "pricing": "price",
            "operations": "ops",
            "sla": "sla",
        }
        sources = []
        groups_by_source = {}
        for rank, document in enumerate(documents, 1):
            for chunk_index in range(2):
                chunk_id = f"{document}-{chunk_index}"
                sources.append(
                    {
                        "citation_id": f"S{len(sources) + 1}",
                        "chunk_id": chunk_id,
                        "document_id": document,
                        "document_key": document,
                        "section_key": f"section-{chunk_index}",
                        "text": f"{document} evidence {chunk_index}",
                        "score": 1.0 - rank / 100,
                        "rank": len(sources) + 1,
                    }
                )
                groups_by_source[chunk_id] = {group_by_document[document]}
        state = ToolTurnState(
            messages=[ChatMessage(role="tool", content="{}", tool_name="search_docs")],
            sources=sources,
            citation_by_source={source["chunk_id"]: source["citation_id"] for source in sources},
            search_groups_by_source=groups_by_source,
            timing={},
            search_group_order=["spec", "price", "ops", "sla"],
            search_query_by_group={
                "spec": "产品规格",
                "price": "价格",
                "ops": "运维步骤",
                "sla": "SLA",
            },
            search_rank_by_group_source={
                (group_by_document[source["document_id"]], source["chunk_id"]):
                    index % 2 + 1
                for index, source in enumerate(sources)
            },
        )

        _prune_sources(state)

        assert {source["document_id"] for source in state.sources} == set(documents)

    def test_single_search_keeps_every_recalled_source_in_grouped_context(self):
        sources = [
            {
                "citation_id": f"S{index + 1}",
                "chunk_id": f"single-{index}",
                "document_id": f"doc-{index}",
                "document_key": f"doc-{index}",
                "section_key": f"section-{index}",
                "text": f"unique_fact_{index}",
                "score": 1.0 - index / 100,
                "rank": index + 1,
            }
            for index in range(20)
        ]
        state = ToolTurnState(
            messages=[ChatMessage(role="tool", content="{}", tool_name="search_docs")],
            sources=sources,
            citation_by_source={source["chunk_id"]: source["citation_id"] for source in sources},
            search_groups_by_source={source["chunk_id"]: {"single"} for source in sources},
            timing={},
            search_group_order=["single"],
            search_query_by_group={"single": "single topic"},
            search_rank_by_group_source={
                ("single", source["chunk_id"]): index + 1
                for index, source in enumerate(sources)
            },
        )

        _prune_sources(state)

        payload = _retrieval_payload(state.messages[-1])
        expected_ids = [f"S{index + 1}" for index in range(20)]
        assert [source["citation_id"] for source in state.sources] == expected_ids
        assert payload["retrieval_groups"] == [
            {"query": "single topic", "source_ids": expected_ids}
        ]
        assert [source["citation_id"] for source in payload["source_catalog"]] == expected_ids

    def test_single_search_deduplicates_overlapping_chunks_before_allocation(self):
        sources = [
            {
                "citation_id": f"S{index + 1}",
                "chunk_id": f"overlap-{index}",
                "document_id": "same-doc",
                "document_key": "same-doc",
                "section_key": "same-section",
                "text": "shared overlapping evidence" + (" detail" * index),
                "score": 1.0 - index / 10,
                "rank": index + 1,
            }
            for index in range(3)
        ]
        state = ToolTurnState(
            messages=[ChatMessage(role="tool", content="{}", tool_name="search_docs")],
            sources=sources,
            citation_by_source={source["chunk_id"]: source["citation_id"] for source in sources},
            search_groups_by_source={source["chunk_id"]: {"single"} for source in sources},
            timing={},
            search_group_order=["single"],
            search_query_by_group={"single": "topic"},
            search_rank_by_group_source={
                ("single", source["chunk_id"]): index + 1
                for index, source in enumerate(sources)
            },
        )

        _prune_sources(state)

        assert [source["citation_id"] for source in state.sources] == ["S1"]
        assert _retrieval_payload(state.messages[-1])["retrieval_groups"] == [
            {"query": "topic", "source_ids": ["S1"]}
        ]

    def test_multi_search_does_not_merge_away_another_query_group(self):
        sources = [
            {
                "citation_id": "S1",
                "chunk_id": "price-chunk",
                "document_id": "manual",
                "document_key": "manual",
                "section_key": "shared-section",
                "text": "shared evidence with pricing detail",
                "score": 0.9,
                "rank": 1,
            },
            {
                "citation_id": "S2",
                "chunk_id": "sla-chunk",
                "document_id": "manual",
                "document_key": "manual",
                "section_key": "shared-section",
                "text": "shared evidence with SLA detail",
                "score": 0.8,
                "rank": 2,
            },
        ]
        state = ToolTurnState(
            messages=[ChatMessage(role="tool", content="{}", tool_name="search_docs")],
            sources=sources,
            citation_by_source={"price-chunk": "S1", "sla-chunk": "S2"},
            search_groups_by_source={
                "price-chunk": {"price"},
                "sla-chunk": {"sla"},
            },
            timing={},
            search_group_order=["price", "sla"],
            search_query_by_group={"price": "pricing", "sla": "SLA"},
            search_rank_by_group_source={
                ("price", "price-chunk"): 1,
                ("sla", "sla-chunk"): 1,
            },
        )

        _prune_sources(state)

        payload = _retrieval_payload(state.messages[-1])
        assert len(state.sources) == 2
        assert payload["retrieval_groups"] == [
            {"query": "pricing", "source_ids": ["S1"]},
            {"query": "SLA", "source_ids": ["S2"]},
        ]

    def test_pruning_preserves_attempted_query_groups_with_no_results(self):
        source = {
            "citation_id": "S2",
            "chunk_id": "current-chunk",
            "document_id": "current-doc",
            "document_key": "current-doc",
            "section_key": "current-section",
            "text": "current evidence",
            "score": 1.0,
            "rank": 1,
        }
        state = ToolTurnState(
            messages=[ChatMessage(role="tool", content="{}", tool_name="search_docs")],
            sources=[source],
            citation_by_source={"stale-chunk": "S1", "current-chunk": "S2"},
            search_groups_by_source={
                "stale-chunk": {"stale"},
                "current-chunk": {"current"},
            },
            timing={},
            search_group_order=["stale", "current"],
            search_query_by_group={"stale": "old query", "current": "new query"},
            search_rank_by_group_source={
                ("stale", "stale-chunk"): 1,
                ("current", "current-chunk"): 1,
            },
        )

        _prune_sources(state)

        assert _retrieval_payload(state.messages[-1])["retrieval_groups"] == [
            {"query": "old query", "source_ids": []},
            {"query": "new query", "source_ids": ["S2"]},
        ]

    def test_empty_search_is_still_serialized_as_an_attempted_group(self):
        state = ToolTurnState(
            messages=[ChatMessage(role="tool", content="{}", tool_name="search_docs")],
            sources=[],
            citation_by_source={},
            search_groups_by_source={},
            timing={},
            search_group_order=["empty-call"],
            search_query_by_group={"empty-call": "missing topic"},
        )

        _prune_sources(state)

        assert _retrieval_payload(state.messages[-1]) == {
            "retrieval_groups": [
                {"query": "missing topic", "source_ids": []}
            ],
            "source_catalog": [],
        }

    def test_multi_topic_pruning_keeps_query_specific_ranked_results(self):
        state = ToolTurnState(
            messages=[ChatMessage(role="tool", content="{}", tool_name="search_docs")],
            sources=[],
            citation_by_source={},
            search_groups_by_source={},
            timing={},
            search_group_order=["spec", "price", "ops", "sla"],
            search_query_by_group={
                "spec": "产品规格",
                "price": "价格",
                "ops": "运维步骤",
                "sla": "SLA",
            },
        )
        shared = {
            "chunk_id": "shared",
            "document_id": "guide",
            "document_key": "guide",
            "text": "XG-7 综合介绍",
        }
        expected = set()
        for group in state.search_group_order:
            specific_id = f"{group}-specific"
            expected.add(specific_id)
            items = [
                dict(shared),
                {
                    "chunk_id": specific_id,
                    "document_id": group,
                    "document_key": group,
                    "text": f"{group} direct evidence",
                },
                *[
                    {
                        "chunk_id": f"{group}-noise-{index}",
                        "document_id": "guide",
                        "document_key": "guide",
                        "text": f"noise {group} {index}",
                    }
                    for index in range(5)
                ],
            ]
            _register_search_sources(
                state,
                "search_docs",
                ToolResult(success=True, data={"results": items}),
                group,
            )

        _prune_sources(state)

        retained = {source["chunk_id"] for source in state.sources}
        assert expected <= retained
        price_source = next(
            source
            for source in state.sources
            if source["chunk_id"] == "price-specific"
        )
        assert {match["query"] for match in price_source["query_matches"]} == {
            "价格",
        }
        serialized = state.messages[-1].content or ""
        assert '"retrieval_groups"' in serialized
        assert '"source_catalog"' in serialized
        assert '"results"' not in serialized
        payload = _retrieval_payload(state.messages[-1])
        grouped_ids = {
            citation_id
            for group in payload["retrieval_groups"]
            for citation_id in group["source_ids"]
        }
        retained_ids = {source["citation_id"] for source in state.sources}
        assert grouped_ids == retained_ids
        assert all(len(group["source_ids"]) >= 2 for group in payload["retrieval_groups"])
        assert any(len(group["source_ids"]) > 2 for group in payload["retrieval_groups"])
        assert serialized.count("XG-7 综合介绍") == 1

    def test_pruning_removes_stale_results_from_older_tool_messages(self):
        sources = [
            {
                "citation_id": f"S{index + 1}",
                "chunk_id": f"chunk-{index}",
                "document_id": "same-doc",
                "document_key": "same-doc",
                "section_key": f"section-{index}",
                "text": f"unique evidence {index} topic-{index}",
                "score": 1.0 - index / 100,
                "rank": index + 1,
            }
            for index in range(10)
        ]
        messages = [
            ChatMessage(role="tool", content="{}", tool_name="search_docs"),
            ChatMessage(role="tool", content="{}", tool_name="search_docs"),
        ]
        state = ToolTurnState(
            messages=messages,
            sources=sources,
            citation_by_source={source["chunk_id"]: source["citation_id"] for source in sources},
            search_groups_by_source={
                source["chunk_id"]: {"first" if index < 5 else "second"} for index, source in enumerate(sources)
            },
            timing={},
        )

        _prune_sources(state)

        assert extract_sources([messages[0]]) == []
        latest_sources = extract_sources([messages[1]])
        assert {source["citation_id"] for source in latest_sources} == {source["citation_id"] for source in state.sources}

    def test_source_normalization_runs_even_without_pruning(self):
        source = {
            "citation_id": "S1",
            "chunk_id": "current-chunk",
            "document_id": "current-doc",
            "document_key": "current-doc",
            "section_key": "current-section",
            "text": "current evidence",
            "score": 1.0,
            "rank": 1,
        }
        messages = [
            ChatMessage(
                role="tool",
                content='{"sources":[{"citation_id":"S9","text":"stale evidence"}]}',
                tool_name="search_docs",
            ),
            ChatMessage(role="tool", content="{}", tool_name="search_docs"),
        ]
        state = ToolTurnState(
            messages=messages,
            sources=[source],
            citation_by_source={"current-chunk": "S1"},
            search_groups_by_source={"current-chunk": {"current"}},
            timing={},
        )

        _prune_sources(state)

        assert extract_sources([messages[0]]) == []
        assert [item["citation_id"] for item in extract_sources([messages[1]])] == ["S1"]

    def test_registered_web_sources_keep_their_query_group(self):
        state = ToolTurnState(
            messages=[ChatMessage(role="tool", content="{}", tool_name="web_search")],
            sources=[],
            citation_by_source={},
            search_groups_by_source={},
            timing={},
            search_group_order=["web-call"],
            search_query_by_group={"web-call": "latest release"},
        )
        result = ToolResult(
            success=True,
            data={
                "results": [
                    {
                        "title": "Release notes",
                        "snippet": "Version details",
                        "url": "https://example.com/release",
                    }
                ]
            },
        )

        _register_search_sources(state, "web_search", result, "web-call")
        _prune_sources(state)

        assert state.sources[0]["chunk_id"] == "web-WS1"
        assert state.search_groups_by_source["web-WS1"] == {"web-call"}
        assert _retrieval_payload(state.messages[-1])["retrieval_groups"] == [
            {"query": "latest release", "source_ids": ["WS1"]}
        ]

    def test_pruning_normalizes_combined_kb_and_web_messages(self):
        kb_sources = [
            {
                "citation_id": f"S{index + 1}",
                "chunk_id": f"kb-{index}",
                "document_id": f"doc-{index}",
                "document_key": f"doc-{index}",
                "section_key": f"section-{index}",
                "text": f"knowledge-base evidence {index}",
                "score": 1.0 - index / 100,
                "rank": index + 1,
            }
            for index in range(5)
        ]
        web_sources = [
            {
                "citation_id": f"WS{index + 6}",
                "chunk_id": f"web-WS{index + 6}",
                "document_id": f"web:https://example.com/{index}",
                "document_key": "web_search",
                "section_key": "",
                "filename": f"Web result {index}",
                "url": f"https://example.com/{index}",
                "text": f"web evidence {index}",
                "score": 0.5,
                "rank": index + 6,
            }
            for index in range(5)
        ]
        sources = kb_sources + web_sources
        messages = [
            ChatMessage(
                role="tool",
                content='{"results":[{"citation_id":"S99","text":"stale kb"}]}',
                tool_name="search_docs",
            ),
            ChatMessage(
                role="tool",
                content='{"results":[{"citation_id":"WS99","text":"stale web"}]}',
                tool_name="web_search",
            ),
        ]
        state = ToolTurnState(
            messages=messages,
            sources=sources,
            citation_by_source={source["chunk_id"]: source["citation_id"] for source in sources},
            search_groups_by_source={source["chunk_id"]: {"kb"} for source in kb_sources},
            timing={},
        )

        _prune_sources(state)

        visible_ids = {
            source["citation_id"]
            for source in extract_sources(messages)
        }
        retained_ids = {source["citation_id"] for source in state.sources}
        assert extract_sources([messages[0]]) == []
        assert visible_ids == retained_ids
        assert "WS99" not in visible_ids
        assert all(source.get("url") for source in state.sources if source["citation_id"].startswith("WS"))

    @pytest.mark.asyncio
    async def test_source_extraction(self, make_fake_llm):
        """v0.2.0: classifier → search_docs → sources 事件包含文档信息。"""
        make_fake_llm(
            [
                _make_classifier_queue(),
                [
                    LLMResponse(
                        tool_calls=[_make_tool_call("search_docs", {"query": "X"})],
                    ),
                ],
                [LLMResponse(content="检索结果如上")],
            ]
        )

        with patch("agent.loop.registry") as mock_registry:
            mock_registry.get_schemas.return_value = []
            mock_registry.execute_parallel = AsyncMock(
                return_value=_make_parallel_result(
                    "search_docs",
                    ToolResult(
                        success=True,
                        data={
                            "results": [
                                {"document_id": "abc12345", "filename": "readme.txt", "text": "重要内容", "score": 0.92},
                            ],
                            "count": 1,
                        },
                    ),
                )
            )

            from agent.loop import run_agent_loop

            events = []
            async for event in run_agent_loop("有哪些文档", []):
                events.append(event)

            sources = _events_by_type(events, "sources")
            assert len(sources) == 1
            assert sources[0]["data"][0]["filename"] == "readme.txt"
            assert sources[0]["data"][0]["rank"] == 1
            assert sources[0]["data"][0]["citation_id"] == "S1"

    @pytest.mark.asyncio
    async def test_multiple_searches_get_unique_aggregated_citations(self, make_fake_llm):
        """v0.2.0: classifier → 多次 search_docs 来源整轮聚合。"""
        make_fake_llm(
            [
                _make_classifier_queue(),
                [LLMResponse(tool_calls=[_make_tool_call("search_docs", {"query": "A"}, "c1")])],
                [LLMResponse(tool_calls=[_make_tool_call("search_docs", {"query": "B"}, "c2")])],
                [LLMResponse(content="结论分别来自 [S1] 和 [S2]。")],
            ]
        )
        first = ToolResult(
            success=True,
            data={
                "results": [{"chunk_id": "ch-1", "document_id": "d1", "text": "A", "score": 0.9}],
                "count": 1,
            },
        )
        second = ToolResult(
            success=True,
            data={
                "results": [{"chunk_id": "ch-2", "document_id": "d2", "text": "B", "score": 0.8}],
                "count": 1,
            },
        )

        with patch("agent.loop.registry") as mock_registry:
            mock_registry.get_schemas.return_value = []
            mock_registry.execute_parallel = AsyncMock(
                side_effect=[
                    _make_parallel_result("search_docs", first),
                    _make_parallel_result("search_docs", second),
                ]
            )

            from agent.loop import run_agent_loop

            events = [event async for event in run_agent_loop("有哪些文档", [])]

        sources = _events_by_type(events, "sources")[0]["data"]
        assert [source["citation_id"] for source in sources] == ["S1", "S2"]
        assert [source["chunk_id"] for source in sources] == ["ch-1", "ch-2"]
        assert sources[0]["query_matches"] == [{"query": "A", "rank": 1}]
        assert sources[1]["query_matches"] == [{"query": "B", "rank": 1}]

    @pytest.mark.asyncio
    async def test_multiple_searches_preserve_evidence_from_each_query_group(
        self,
        make_fake_llm,
    ):
        """v0.2.0: classifier → 同一文档多次检索保留各组高分片段。"""
        make_fake_llm(
            [
                _make_classifier_queue(),
                [LLMResponse(tool_calls=[_make_tool_call("search_docs", {"query": "MCP"}, "c1")])],
                [LLMResponse(tool_calls=[_make_tool_call("search_docs", {"query": "Skill"}, "c2")])],
                [LLMResponse(content="MCP 与 Skill 的资料均已找到 [S1] [S6]。")],
            ]
        )
        first = ToolResult(
            success=True,
            data={
                "results": [
                    {
                        "chunk_id": f"mcp-{index}",
                        "document_id": "same-doc",
                        "document_key": "same-doc",
                        "section_key": f"mcp-{index}",
                        "text": f"MCP evidence topic {index} alpha",
                        "score": 1.0 - index / 100,
                    }
                    for index in range(5)
                ],
                "count": 5,
            },
        )
        second = ToolResult(
            success=True,
            data={
                "results": [
                    {
                        "chunk_id": f"skill-{index}",
                        "document_id": "same-doc",
                        "document_key": "same-doc",
                        "section_key": f"skill-{index}",
                        "text": f"Skill evidence topic {index} omega",
                        "score": 0.5 - index / 100,
                    }
                    for index in range(5)
                ],
                "count": 5,
            },
        )

        with patch("agent.loop.registry") as mock_registry:
            mock_registry.get_schemas.return_value = []
            mock_registry.execute_parallel = AsyncMock(
                side_effect=[
                    _make_parallel_result("search_docs", first),
                    _make_parallel_result("search_docs", second),
                ]
            )

            from agent.loop import run_agent_loop

            events = [event async for event in run_agent_loop("有哪些文档", [])]

        sources = _events_by_type(events, "sources")[0]["data"]
        assert len(sources) <= 8
        assert any(source["chunk_id"].startswith("mcp-") for source in sources)
        assert any(source["chunk_id"].startswith("skill-") for source in sources), sources


class TestAgentLoopLimits:
    def test_forced_convergence_keeps_only_calculator_for_incomplete_budget(self):
        schemas = [
            {"type": "function", "function": {"name": "search_docs"}},
            {"type": "function", "function": {"name": "calculator"}},
            {"type": "function", "function": {"name": "list_documents"}},
        ]

        allowed = _tools_during_forced_convergence(
            schemas,
            "请计算平台订阅折扣和最终总预算",
            {15360.0},
        )

        assert allowed is not None
        assert [item["function"]["name"] for item in allowed] == ["calculator"]

    def test_forced_convergence_disables_tools_for_non_numeric_query(self):
        schemas = [
            {"type": "function", "function": {"name": "search_docs"}},
            {"type": "function", "function": {"name": "calculator"}},
        ]

        assert _tools_during_forced_convergence(
            schemas,
            "请总结产品特点",
            set(),
        ) is None

    def test_loop_detection_requires_similar_retrieval_queries(self):
        history = [
            ("search_docs", "光伏成本下降了吗？"),
            ("search_docs", "光伏成本的历史趋势"),
            ("search_docs", "光伏产业补贴政策"),
        ]

        assert detect_repetitive_tool_calls(history) is None

    def test_loop_detection_catches_rephrased_repetition(self):
        history = [
            ("search_docs", "光伏成本下降了吗？"),
            ("search_docs", "光伏成本下降了吗"),
            ("search_docs", "请问光伏成本下降了吗？"),
        ]

        assert detect_repetitive_tool_calls(history) == ("search_docs", 3)

    def test_loop_detection_ignores_non_retrieval_tools(self):
        history = [
            ("calculator", ""),
            ("calculator", ""),
            ("calculator", ""),
        ]

        assert detect_repetitive_tool_calls(history) is None

    @pytest.mark.asyncio
    async def test_soft_limit_extends_only_when_tool_turn_makes_progress(
        self,
        make_fake_llm,
        monkeypatch,
    ):
        from config import settings

        monkeypatch.setattr(settings, "max_loop_iterations", 1)
        monkeypatch.setattr(settings, "max_loop_hard_iterations", 3)
        make_fake_llm(
            [
                _make_classifier_queue(),
                [LLMResponse(tool_calls=[_make_tool_call("search_docs", {"query": "topic"}, "s1")])],
                [LLMResponse(content="supported fact [S1].")],
            ]
        )
        search_result = ToolResult(
            success=True,
            data={
                "results": [{
                    "chunk_id": "c1",
                    "document_id": "d1",
                    "filename": "doc.md",
                    "text": "supported fact",
                    "score": 0.9,
                }],
                "count": 1,
            },
        )

        with patch("agent.loop.registry") as mock_registry:
            mock_registry.get_schemas.return_value = []
            mock_registry.execute_parallel = AsyncMock(
                return_value=_make_parallel_result("search_docs", search_result),
            )
            from agent.loop import run_agent_loop

            events = [event async for event in run_agent_loop("topic", [])]

        assert any(
            event.get("event") == "status"
            and event["data"].get("code") == "LOOP_EXTENDED"
            for event in events
        )
        assert "supported fact" in "".join(
            event["data"]["delta"]
            for event in events
            if event.get("event") == "answer_chunk"
        )

    @pytest.mark.asyncio
    async def test_loop_limit(self, make_fake_llm, monkeypatch):
        """Repeated calls stop cleanly and a safe synthesis follows."""
        from config import settings

        monkeypatch.setattr(settings, "max_loop_iterations", 3)
        monkeypatch.setattr(settings, "max_loop_hard_iterations", 3)
        max_iter = settings.max_loop_iterations
        queues = [_make_classifier_queue()]
        for _ in range(max_iter):
            queues.append(
                [
                    LLMResponse(
                        tool_calls=[_make_tool_call("calculator", {"expression": "1+1"})],
                    ),
                ]
            )
        queues.append([LLMResponse(content="final")])

        make_fake_llm(queues)

        with patch("agent.loop.registry") as mock_registry:
            mock_registry.get_schemas.return_value = []
            mock_registry.execute_parallel = AsyncMock(
                return_value=_make_parallel_result(
                    "calculator",
                    ToolResult(success=True, data={"result": 2}),
                )
            )

            from agent.loop import run_agent_loop

            events = []
            async for event in run_agent_loop("帮我算一下3*4", []):
                events.append(event)

            assert not [e for e in events if e.get("event") == "error"]
            statuses = [e for e in events if e.get("event") == "status"]
            assert any(
                e["data"].get("code") in {"LOOP_LIMIT", "NO_PROGRESS"}
                for e in statuses
            )
            assert "final" in "".join(
                e["data"]["delta"]
                for e in events
                if e.get("event") == "answer_chunk"
            )

    @pytest.mark.asyncio
    async def test_forced_synthesis_never_invents_missing_calculator_results(
        self,
        make_fake_llm,
        monkeypatch,
    ):
        from config import settings

        monkeypatch.setattr(settings, "max_loop_iterations", 1)
        monkeypatch.setattr(settings, "max_loop_hard_iterations", 1)
        make_fake_llm(
            [
                _make_classifier_queue(),
                [LLMResponse(tool_calls=[
                    _make_tool_call("search_docs", {"query": "pricing"}, "s1"),
                ])],
            ]
        )
        source = ToolResult(
            success=True,
            data={
                "results": [{
                    "chunk_id": "price-1",
                    "document_id": "d1",
                    "filename": "pricing.xlsx",
                    "text": (
                        "XG-7 Pro价格7600元，基础订阅1280元/月，包含40个节点，"
                        "超额节点36元/月，年付折扣0.1，实施费8000元。"
                    ),
                    "score": 0.9,
                }],
                "count": 1,
            },
        )

        with patch("agent.loop.registry") as mock_registry:
            mock_registry.get_schemas.return_value = []
            mock_registry.execute_parallel = AsyncMock(
                return_value=_make_parallel_result("search_docs", source),
            )
            from agent.loop import run_agent_loop

            events = [
                event
                async for event in run_agent_loop(
                    "75个节点，列出订阅原价、年付折扣和最终总额",
                    [],
                )
            ]

        answer = "".join(
            event["data"]["delta"]
            for event in events
            if event.get("event") == "answer_chunk"
        )
        assert "计算器步骤未完整执行" in answer
        assert "43032" not in answer
        assert any(event.get("event") == "done" for event in events)


class TestAgentLoopParallelTools:
    @pytest.mark.asyncio
    async def test_parallel_tool_calls(self, make_fake_llm):
        """LLM returns multiple tool_calls → all executed, all results reported."""
        make_fake_llm(
            [
                # Intent classification (needed because query doesn't match rules)
                [
                    LLMResponse(
                        tool_calls=[
                            _make_tool_call(
                                "classify_intent",
                                {
                                    "intent": "knowledge_retrieval",
                                    "suggested_tools": ["search_docs"],
                                    "hint_text": "",
                                },
                                call_id="ic",
                            )
                        ]
                    )
                ],
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
            ]
        )

        with patch("agent.loop.registry") as mock_registry:
            mock_registry.get_schemas.return_value = []
            mock_registry.execute_parallel = AsyncMock(
                return_value=[
                    ("search_docs", ToolResult(success=True, data={"results": [], "count": 0}), 0.0),
                    ("recall_memory", ToolResult(success=True, data={"results": [], "count": 0}), 0.0),
                ]
            )

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
        make_fake_llm(
            [
                # Intent classification
                [
                    LLMResponse(
                        tool_calls=[
                            _make_tool_call(
                                "classify_intent",
                                {
                                    "intent": "knowledge_retrieval",
                                    "suggested_tools": ["search_docs", "calculator"],
                                    "hint_text": "",
                                },
                                call_id="ic",
                            )
                        ]
                    )
                ],
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
            ]
        )

        with patch("agent.loop.registry") as mock_registry:
            mock_registry.get_schemas.return_value = []
            mock_registry.execute_parallel = AsyncMock(
                return_value=[
                    ("search_docs", ToolResult(success=True, data={"results": [], "count": 0}), 0.0),
                    ("calculator", ToolResult(success=False, error="division by zero"), 0.0),
                ]
            )

            from agent.loop import run_agent_loop

            events = []
            async for event in run_agent_loop("calc and search", []):
                events.append(event)

            tool_results = _events_by_type(events, "tool_result")
            assert len(tool_results) == 2
            successes = [tr["data"]["success"] for tr in tool_results]
            assert True in successes
            assert False in successes
