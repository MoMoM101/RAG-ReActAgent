"""Agent Loop 核心测试 — Mock LLM 注入，验证关键路径。"""

from unittest.mock import AsyncMock, patch

import pytest

from agent.loop_tools import ToolTurnState, _prune_sources
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


def _make_parallel_result(name: str, result: ToolResult, elapsed: float = 0.0):
    """Return a single-element execute_parallel result list."""
    return [(name, result, elapsed)]


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
    @pytest.mark.asyncio
    async def test_loop_limit(self, make_fake_llm):
        """v0.2.0: classifier then max_loop_iterations tool_calls → LOOP_LIMIT error."""
        from config import settings

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

            errors = [e for e in events if e.get("event") == "error"]
            assert len(errors) >= 1
            assert any(e["data"]["code"] == "LOOP_LIMIT" for e in errors)


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
