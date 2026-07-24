"""V4 integration tests (Plan Section 8.2).

Covers: grounding enforcement modes, client disconnect,
cache lifecycle, timing payload, and concurrent citation_id isolation.

NOTE: FakeLLM queues are consumed in this order:
  1. Intent classifier LLM call (if query triggers _llm_needed)
  2. Main loop turn 1 (tool call)
  3. Main loop turn 2 (final answer)
  4. Repair LLM call (if repair triggered)

Queries like \"列出文档\", \"搜索 X\" match rule-based intents and skip
the classifier LLM call. Other queries need an extra queue at position 0.
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, patch

import pytest

from agent.tools import ToolResult
from config import settings
from llm.base import ChatMessage, LLMResponse, ToolCall


def _make_tool_call(name: str, args: dict, call_id: str = "call_1") -> ToolCall:
    return ToolCall(id=call_id, name=name, arguments=args)


def _events_by_type(events: list[dict], event_type: str) -> list[dict]:
    return [e for e in events if e.get("event") == event_type]


def _make_search_result() -> ToolResult:
    return ToolResult(
        success=True,
        data={
            "count": 1,
            "reranked": False,
            "results": [
                {
                    "chunk_id": "chunk-001",
                    "document_id": "doc-001",
                    "document_key": "test-doc",
                    "section_key": "section-1",
                    "text": "光伏成本在过去十年下降了 90%。",
                    "score": 0.95,
                    "citation_id": "S1",
                }
            ],
        },
    )


class TestGroundingEnforcementModes:
    def test_stream_refusal_uses_selective_retry_policy(self):
        from agent.loop import _verify_stream_unit
        from agent.stream_verify import AtomicUnit, UnitVerdict
        from agent.verifier import Evidence

        unit = AtomicUnit(text="现有资料不足以回答该问题。")
        evidence = [Evidence("S1", "ROC-AUC 是常用模型评估指标。")]
        direct = _verify_stream_unit(unit, evidence, "ROC-AUC 是什么")
        comparison = _verify_stream_unit(
            unit,
            evidence,
            "ROC-AUC 和 F1 有什么不同",
        )

        assert direct.verdict == UnitVerdict.UNSUPPORTED
        assert direct.reason == "topical_false_refusal"
        assert comparison.verdict == UnitVerdict.VERIFIED

    @pytest.mark.asyncio
    async def test_off_mode_streams_directly(self, make_fake_llm):
        """grounding_enforcement=off streams answer chunks without buffering."""
        make_fake_llm(
            [
                [LLMResponse(content="你好！有什么可以帮你的？")],
            ]
        )

        with patch("agent.loop.registry") as mock_registry:
            mock_registry.get_schemas.return_value = []
            mock_registry.execute_parallel = AsyncMock()

            with patch.object(settings, "grounding_enforcement", "off"):
                from agent.loop import run_agent_loop

                events = []
                async for ev in run_agent_loop("你好", []):
                    events.append(ev)

        chunks = _events_by_type(events, "answer_chunk")
        done = _events_by_type(events, "done")
        assert len(chunks) > 0
        assert len(done) == 1

    @pytest.mark.asyncio
    async def test_report_mode_emits_sources(self, make_fake_llm):
        """RAG with enforcement=report yields sources and timing events."""
        # Extra queue[0]: intent classifier LLM call (query triggers _llm_needed)
        make_fake_llm(
            [
                [LLMResponse(content="知识库检索")],  # classifier response
                [
                    LLMResponse(
                        tool_calls=[_make_tool_call("search_docs", {"query": "光伏"})],
                        is_final=True,
                    ),
                ],
                [LLMResponse(content="光伏成本下降了 90% [S1]。", is_final=False)],
            ]
        )

        with patch("agent.loop.registry") as mock_registry:
            mock_registry.get_schemas.return_value = [{"function": {"name": "search_docs", "description": "Search docs"}}]
            mock_registry.execute_parallel = AsyncMock(return_value=[("search_docs", _make_search_result(), 10.0)])

            with (
                patch.object(settings, "grounding_enforcement", "report"),
                patch.object(settings, "grounding_repair_enabled", False),
                patch.object(
                    settings,
                    "grounding_deterministic_repair_enabled",
                    False,
                ),
            ):
                from agent.loop import run_agent_loop

                events = []
                async for ev in run_agent_loop("光伏成本下降了吗？", []):
                    events.append(ev)

        sources = _events_by_type(events, "sources")
        timing = _events_by_type(events, "timing")
        done = _events_by_type(events, "done")
        assert len(sources) == 1, f"Expected 1 sources event, got {len(sources)}"
        assert len(timing) >= 1, f"Expected ≥1 timing event, got {len(timing)}"
        assert len(done) == 1


class TestClientDisconnect:
    @pytest.mark.asyncio
    async def test_disconnect_between_iterations_cancels(self, make_fake_llm):
        """Cancellation set between agent loop iterations yields CANCELLED."""
        # 3 queues: classifier → tool_call → (cancelled before answer)
        make_fake_llm(
            [
                [LLMResponse(content="检索")],  # classifier
                [
                    LLMResponse(
                        tool_calls=[_make_tool_call("search_docs", {"query": "test"})],
                        is_final=True,
                    ),
                ],
                [LLMResponse(content="答案 [S1]。", is_final=False)],
            ]
        )

        with patch("agent.loop.registry") as mock_registry:
            mock_registry.get_schemas.return_value = [{"function": {"name": "search_docs", "description": "Search docs"}}]
            mock_registry.execute_parallel = AsyncMock(return_value=[("search_docs", _make_search_result(), 10.0)])

            cancelled = asyncio.Event()
            from agent.loop import run_agent_loop

            events = []
            async for ev in run_agent_loop("test disconnect", [], cancelled=cancelled):
                events.append(ev)
                # Cancel after tool execution (end of iteration 1)
                if ev["event"] == "tool_result":
                    cancelled.set()

        error_events = _events_by_type(events, "error")
        cancelled_errors = [e for e in error_events if e["data"].get("code") == "CANCELLED"]
        assert len(cancelled_errors) == 1


class TestCitationIdIsolation:
    @pytest.mark.asyncio
    async def test_sequential_requests_independent_citations(self, make_fake_llm):
        """Two sequential RAG requests produce independent citation_id counters."""
        from agent.loop import run_agent_loop

        result_a = _make_search_result()
        result_b = ToolResult(
            success=True,
            data={
                "count": 1,
                "reranked": False,
                "results": [
                    {
                        "chunk_id": "chunk-002",
                        "document_id": "doc-002",
                        "document_key": "test-doc-b",
                        "section_key": "sec-1",
                        "text": "不同来源B。",
                        "score": 0.9,
                        "citation_id": "S1",
                    }
                ],
            },
        )

        async def run_one(query: str, result: ToolResult) -> list[str]:
            make_fake_llm(
                [
                    [LLMResponse(content="检索")],  # classifier
                    [
                        LLMResponse(
                            tool_calls=[_make_tool_call("search_docs", {"query": "x"})],
                            is_final=True,
                        ),
                    ],
                    [LLMResponse(content="答案 [S1]。", is_final=False)],
                ]
            )
            with patch("agent.loop.registry") as mock_registry:
                mock_registry.get_schemas.return_value = [{"function": {"name": "search_docs", "description": "Search docs"}}]
                mock_registry.execute_parallel = AsyncMock(return_value=[("search_docs", result, 10.0)])
                with patch.object(settings, "grounding_verification_enabled", False):
                    events = []
                    async for ev in run_agent_loop(query, []):
                        events.append(ev)
                    src = _events_by_type(events, "sources")
                    return [s["citation_id"] for s in src[0]["data"]] if src else []

        cids_a = await run_one("问题A", result_a)
        cids_b = await run_one("问题B", result_b)

        assert cids_a == ["S1"], f"Expected ['S1'], got {cids_a}"
        assert cids_b == ["S1"], f"Expected ['S1'], got {cids_b}"


class TestTimingPayload:
    @pytest.mark.asyncio
    async def test_rag_answer_yields_timing(self, make_fake_llm):
        """RAG answers include timing event with phase information."""
        answer_text = "光伏成本下降了 90% [S1]。"
        make_fake_llm(
            [
                [LLMResponse(content="检索")],  # classifier
                [
                    LLMResponse(
                        tool_calls=[_make_tool_call("search_docs", {"query": "光伏"})],
                        is_final=True,
                    ),
                ],
                [LLMResponse(content=answer_text, is_final=False)],
            ]
        )

        # Source text matches the answer so verification passes
        matching_result = ToolResult(
            success=True,
            data={
                "count": 1,
                "reranked": False,
                "results": [
                    {
                        "chunk_id": "chunk-001",
                        "document_id": "doc-001",
                        "document_key": "test-doc",
                        "section_key": "section-1",
                        "text": "光伏成本在过去十年下降了 90%。",
                        "score": 0.95,
                        "citation_id": "S1",
                    }
                ],
            },
        )

        with patch("agent.loop.registry") as mock_registry:
            mock_registry.get_schemas.return_value = [{"function": {"name": "search_docs", "description": "Search docs"}}]
            mock_registry.execute_parallel = AsyncMock(return_value=[("search_docs", matching_result, 10.0)])

            with (
                patch.object(settings, "grounding_repair_enabled", False),
                patch.object(
                    settings,
                    "grounding_deterministic_repair_enabled",
                    False,
                ),
            ):
                from agent.loop import run_agent_loop

                events = []
                async for ev in run_agent_loop("光伏成本下降了吗？", []):
                    events.append(ev)

        timing = _events_by_type(events, "timing")
        assert len(timing) >= 1, f"Expected ≥1 timing event, got {len(timing)}"


class TestAnswerCache:
    @pytest.mark.asyncio
    async def test_cache_lifecycle(self, make_fake_llm):
        """Cache miss → store → subsequent hit."""
        fake = make_fake_llm(
            [
                [LLMResponse(content="检索")],  # classifier
                [
                    LLMResponse(
                        tool_calls=[_make_tool_call("search_docs", {"query": "test"})],
                        is_final=True,
                    ),
                ],
                [LLMResponse(content="光伏成本下降 90% [S1]。", is_final=False)],
                [LLMResponse(content="检索")],  # second classifier
                [
                    LLMResponse(
                        tool_calls=[_make_tool_call("search_docs", {"query": "test"})],
                        is_final=True,
                    ),
                ],
            ]
        )

        from rag.answer_cache import get_answer_cache

        get_answer_cache().clear()

        with patch("agent.loop.registry") as mock_registry:
            mock_registry.get_schemas.return_value = [{"function": {"name": "search_docs", "description": "Search docs"}}]
            mock_registry.execute_parallel = AsyncMock(return_value=[("search_docs", _make_search_result(), 10.0)])

            with (
                patch.object(settings, "rag_answer_cache_enabled", True),
                patch.object(settings, "grounding_verification_enabled", False),
            ):
                from agent.loop import run_agent_loop

                events = []
                async for ev in run_agent_loop("光伏成本下降了吗？", []):
                    events.append(ev)

                cached_events = []
                async for ev in run_agent_loop("光伏成本下降了吗？", []):
                    cached_events.append(ev)

        done = _events_by_type(events, "done")
        assert len(done) == 1
        cached_timing = _events_by_type(cached_events, "timing")
        assert cached_timing[0]["data"]["cache_hit"] is True
        assert cached_timing[0]["data"]["repair_used"] == "cache_hit"
        # The second request performs classification + retrieval planning, but
        # consumes no final-answer generation queue.
        assert fake.call_index == 5


class TestErrorHandling:
    @pytest.mark.asyncio
    async def test_comparison_repair_cannot_collapse_to_one_side(
        self,
        make_fake_llm,
    ):
        history = [
            ChatMessage(role="user", content="skill是什么"),
            ChatMessage(role="assistant", content="Skill 是能力模块。"),
        ]
        make_fake_llm(
            [
                # 0: intent classifier
                [LLMResponse(tool_calls=[
                    _make_tool_call("classify_intent", {
                        "intent": "general_chat",
                        "suggested_tools": ["search_docs"],
                        "hint_text": "compare MCP and Skill",
                    }, call_id="ci"),
                ])],
                # 1: main loop — search_docs
                [
                    LLMResponse(
                        tool_calls=[
                            _make_tool_call(
                                "search_docs",
                                {"query": "Skill 与 MCP 区别"},
                            )
                        ]
                    )
                ],
                # 2: main loop — answer
                [
                    LLMResponse(
                        content=("Skill 是基于文件系统的能力模块 [S1]。MCP 是标准化通信协议 [S2]。两者的区别是完全相同 [S1]。")
                    )
                ],
                # 3: repair
                [LLMResponse(content="MCP 是标准化通信协议 [S2]。")],
            ]
        )
        result = ToolResult(
            success=True,
            data={
                "results": [
                    {
                        "chunk_id": "skill",
                        "document_id": "doc",
                        "document_key": "doc",
                        "section_key": "Skill",
                        "text": "Skill 是基于文件系统的能力模块。",
                        "score": 0.9,
                    },
                    {
                        "chunk_id": "mcp",
                        "document_id": "doc",
                        "document_key": "doc",
                        "section_key": "MCP",
                        "text": "MCP 是标准化通信协议。",
                        "score": 0.8,
                    },
                ],
                "count": 2,
            },
        )

        with patch("agent.loop.registry") as mock_registry:
            mock_registry.get_schemas.return_value = [{"function": {"name": "search_docs", "description": "Search docs"}}]
            mock_registry.execute_parallel = AsyncMock(return_value=[("search_docs", result, 10.0)])
            with (
                patch.object(settings, "grounding_stream_verify_enabled", True),
                patch.object(settings, "rag_answer_cache_enabled", False),
            ):
                from agent.loop import run_agent_loop

                events = [
                    event
                    async for event in run_agent_loop(
                        "mcp和它有什么区别",
                        history,
                    )
                ]

        answer = "".join(event["data"]["delta"] for event in _events_by_type(events, "answer_chunk"))
        timing = _events_by_type(events, "timing")[-1]["data"]
        assert "Skill" in answer
        assert "MCP" in answer
        # v0.2.0: safety guard is passthrough; repair may emit different reasons
        assert timing["repair_reasons"]

    @pytest.mark.asyncio
    async def test_stream_verification_never_finishes_without_visible_answer(
        self,
        make_fake_llm,
    ):
        from agent.stream_verify import UnitResult, UnitVerdict

        make_fake_llm(
            [
                [LLMResponse(content="知识库检索")],
                [
                    LLMResponse(
                        tool_calls=[_make_tool_call("search_docs", {"query": "光伏"})],
                        is_final=True,
                    ),
                ],
                [LLMResponse(content="未经支持的结论。")],
            ]
        )

        def reject_unit(unit, *_args, **_kwargs):
            return UnitResult(
                unit=unit,
                verdict=UnitVerdict.UNSUPPORTED,
                reason="forced_unsupported",
            )

        with (
            patch("agent.loop.registry") as mock_registry,
            patch("agent.loop._verify_stream_unit", side_effect=reject_unit),
        ):
            mock_registry.get_schemas.return_value = [{"function": {"name": "search_docs", "description": "Search docs"}}]
            mock_registry.execute_parallel = AsyncMock(return_value=[("search_docs", _make_search_result(), 10.0)])
            with (
                patch.object(settings, "grounding_stream_verify_enabled", True),
                patch.object(settings, "grounding_repair_enabled", False),
                patch.object(settings, "rag_answer_cache_enabled", False),
            ):
                from agent.loop import run_agent_loop

                events = []
                async for event in run_agent_loop("光伏成本如何？", []):
                    events.append(event)

        answer = "".join(event["data"]["delta"] for event in _events_by_type(events, "answer_chunk"))
        # v0.2.0: stream-verify fallback message changed; ensure non-empty answer
        assert len(answer) > 0
        assert _events_by_type(events, "done")

    @pytest.mark.asyncio
    async def test_empty_final_answer_retries_once(self, make_fake_llm):
        make_fake_llm(
            [
                [LLMResponse(content="检索")],
                [
                    LLMResponse(
                        tool_calls=[_make_tool_call("search_docs", {"query": "test"})],
                        is_final=True,
                    ),
                ],
                [],
                [LLMResponse(content="光伏成本下降 90% [S1]。")],
            ]
        )

        with patch("agent.loop.registry") as mock_registry:
            mock_registry.get_schemas.return_value = [{"function": {"name": "search_docs", "description": "Search docs"}}]
            mock_registry.execute_parallel = AsyncMock(return_value=[("search_docs", _make_search_result(), 10.0)])
            with (
                patch.object(settings, "grounding_stream_verify_enabled", False),
                patch.object(settings, "rag_answer_cache_enabled", False),
            ):
                from agent.loop import run_agent_loop

                events = []
                async for event in run_agent_loop("光伏成本下降了吗？", []):
                    events.append(event)

        chunks = _events_by_type(events, "answer_chunk")
        assert "".join(chunk["data"]["delta"] for chunk in chunks) == ("光伏成本下降 90% [S1]。")

    @pytest.mark.asyncio
    async def test_reasoning_truncation_recovers_with_final_only_retry(
        self,
        make_fake_llm,
    ):
        fake = make_fake_llm(
            [
                [LLMResponse(content="检索")],
                [
                    LLMResponse(
                        tool_calls=[_make_tool_call("search_docs", {"query": "test"})],
                        is_final=True,
                    ),
                ],
                [
                    LLMResponse(
                        reasoning_content="I have enough information. Let me answer.",
                        is_final=False,
                    ),
                    LLMResponse(content="", is_final=True, finish_reason="length"),
                ],
                [LLMResponse(content="光伏成本下降 90% [S1]。")],
            ]
        )

        with patch("agent.loop.registry") as mock_registry:
            mock_registry.get_schemas.return_value = [{"function": {"name": "search_docs", "description": "Search docs"}}]
            mock_registry.execute_parallel = AsyncMock(return_value=[("search_docs", _make_search_result(), 10.0)])
            with (
                patch.object(settings, "grounding_stream_verify_enabled", False),
                patch.object(settings, "grounding_verification_enabled", False),
                patch.object(settings, "rag_answer_cache_enabled", False),
                patch.object(settings, "rag_truncation_recovery_enabled", True),
                patch.object(settings, "rag_truncation_recovery_max_tokens", 2048),
            ):
                from agent.loop import run_agent_loop

                events = []
                async for event in run_agent_loop("光伏成本下降了吗？", []):
                    events.append(event)

        answer = "".join(event["data"]["delta"] for event in _events_by_type(events, "answer_chunk"))
        assert answer == "光伏成本下降 90% [S1]。"
        assert fake.call_index == 4
        assert _events_by_type(events, "done")

    @pytest.mark.asyncio
    async def test_unsupported_unit_does_not_hide_later_verified_unit(
        self,
        make_fake_llm,
    ):
        from agent.stream_verify import UnitResult, UnitVerdict

        make_fake_llm(
            [
                [LLMResponse(content="检索")],
                [
                    LLMResponse(
                        tool_calls=[_make_tool_call("search_docs", {"query": "test"})],
                        is_final=True,
                    ),
                ],
                [LLMResponse(content="不受支持的开场。光伏成本下降 90% [S1]。")],
            ]
        )

        def selective_verify(unit, *_args, **_kwargs):
            if "不受支持" in unit.text:
                return UnitResult(
                    unit=unit,
                    verdict=UnitVerdict.UNSUPPORTED,
                    reason="forced_unsupported",
                )
            return UnitResult(unit=unit, verdict=UnitVerdict.VERIFIED)

        with (
            patch("agent.loop.registry") as mock_registry,
            patch("agent.loop._verify_stream_unit", side_effect=selective_verify),
        ):
            mock_registry.get_schemas.return_value = [{"function": {"name": "search_docs", "description": "Search docs"}}]
            mock_registry.execute_parallel = AsyncMock(return_value=[("search_docs", _make_search_result(), 10.0)])
            with (
                patch.object(settings, "grounding_stream_verify_enabled", True),
                patch.object(settings, "grounding_repair_enabled", False),
                patch.object(settings, "rag_answer_cache_enabled", False),
            ):
                from agent.loop import run_agent_loop

                events = []
                async for event in run_agent_loop("光伏成本下降了吗？", []):
                    events.append(event)

        answer = "".join(event["data"]["delta"] for event in _events_by_type(events, "answer_chunk"))
        assert "不受支持的开场" not in answer
        assert "光伏成本下降 90% [S1]" in answer
        assert "未能通过来源校验" not in answer

    @pytest.mark.asyncio
    async def test_failed_truncation_recovery_has_specific_fallback(
        self,
        make_fake_llm,
    ):
        make_fake_llm(
            [
                [LLMResponse(content="检索")],
                [
                    LLMResponse(
                        tool_calls=[_make_tool_call("search_docs", {"query": "test"})],
                        is_final=True,
                    ),
                ],
                [LLMResponse(content="", is_final=True, finish_reason="length")],
                [LLMResponse(content="", is_final=True, finish_reason="length")],
            ]
        )

        with patch("agent.loop.registry") as mock_registry:
            mock_registry.get_schemas.return_value = [{"function": {"name": "search_docs", "description": "Search docs"}}]
            mock_registry.execute_parallel = AsyncMock(return_value=[("search_docs", _make_search_result(), 10.0)])
            with (
                patch.object(settings, "grounding_stream_verify_enabled", True),
                patch.object(settings, "rag_answer_cache_enabled", False),
                patch.object(settings, "rag_truncation_recovery_enabled", True),
            ):
                from agent.loop import run_agent_loop

                events = []
                async for event in run_agent_loop("光伏成本下降了吗？", []):
                    events.append(event)

        answer = "".join(event["data"]["delta"] for event in _events_by_type(events, "answer_chunk"))
        timing = _events_by_type(events, "timing")[-1]["data"]
        assert "未能生成完整的最终答案" in answer
        assert "generation_truncated" in timing["repair_reasons"]

    @pytest.mark.asyncio
    async def test_tool_error_reported(self, make_fake_llm):
        """Failed tool execution emits tool_result with error."""
        make_fake_llm(
            [
                [LLMResponse(content="检索")],  # classifier
                [
                    LLMResponse(
                        tool_calls=[_make_tool_call("search_docs", {"query": "test"})],
                        is_final=True,
                    ),
                ],
                [LLMResponse(content="无法检索。")],
            ]
        )

        with patch("agent.loop.registry") as mock_registry:
            mock_registry.get_schemas.return_value = [{"function": {"name": "search_docs", "description": "Search docs"}}]
            mock_registry.execute_parallel = AsyncMock(
                return_value=[("search_docs", ToolResult(success=False, error="索引不可用"), 5.0)]
            )

            with patch.object(settings, "grounding_verification_enabled", False):
                from agent.loop import run_agent_loop

                events = []
                async for ev in run_agent_loop("test query", []):
                    events.append(ev)

        tool_results = _events_by_type(events, "tool_result")
        assert len(tool_results) >= 1
        assert tool_results[0]["data"]["success"] is False


class TestEmptyRetrieval:
    @pytest.mark.asyncio
    async def test_empty_search_no_sources(self, make_fake_llm):
        """Empty search results yield no sources event."""
        make_fake_llm(
            [
                [LLMResponse(content="检索")],  # classifier
                [
                    LLMResponse(
                        tool_calls=[_make_tool_call("search_docs", {"query": "nonexistent"})],
                        is_final=True,
                    ),
                ],
                [LLMResponse(content="未找到相关信息。")],
            ]
        )

        empty_result = ToolResult(
            success=True,
            data={"count": 0, "reranked": False, "results": []},
        )

        with patch("agent.loop.registry") as mock_registry:
            mock_registry.get_schemas.return_value = [{"function": {"name": "search_docs", "description": "Search docs"}}]
            mock_registry.execute_parallel = AsyncMock(return_value=[("search_docs", empty_result, 5.0)])

            with patch.object(settings, "grounding_verification_enabled", False):
                from agent.loop import run_agent_loop

                events = []
                async for ev in run_agent_loop("nonexistent search", []):
                    events.append(ev)

        done = _events_by_type(events, "done")
        sources = _events_by_type(events, "sources")
        assert len(done) == 1
        assert len(sources) == 0
