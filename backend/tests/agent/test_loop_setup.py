from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from agent.classifier import IntentHint
from agent.loop_setup import apply_memory_context, classify_turn
from agent.query_semantics import resolve_followup_query
from llm.base import ChatMessage


def _hint(intent: str = "knowledge_qa") -> IntentHint:
    return IntentHint(
        intent=intent,
        confidence=0.5,
        suggested_tools=[],
        hint_text="base hint",
    )


def test_resolve_followup_query_uses_latest_simple_topic():
    history = [
        ChatMessage(role="user", content="skill是什么"),
        ChatMessage(role="assistant", content="Skill 是能力模块。"),
    ]

    assert resolve_followup_query("mcp和它有什么区别", history) == ("mcp和skill有什么区别")


def test_resolve_followup_query_keeps_unresolved_complex_history():
    history = [
        ChatMessage(role="user", content="请详细说明昨天讨论的整个部署方案"),
        ChatMessage(role="assistant", content="好的。"),
    ]

    assert resolve_followup_query("它有什么区别", history) == "它有什么区别"


@pytest.mark.asyncio
async def test_classification_timeout_uses_knowledge_fallback():
    needs_llm = _hint("_llm_needed")
    fallback = _hint("_llm_needed")
    classify = MagicMock(side_effect=[needs_llm, fallback])

    with (
        patch("agent.loop_setup.classify_intent", classify),
        patch(
            "agent.loop_setup.llm_classify",
            new=AsyncMock(side_effect=TimeoutError),
        ),
    ):
        result = await classify_turn("question", [])

    assert result.intent == "knowledge_qa"


@pytest.mark.asyncio
async def test_memory_context_saves_direct_and_confirmed_candidates():
    hint = _hint()
    hint.save_to_profile = [{"content": "工程师", "type": "identity_role"}]
    save = AsyncMock()

    with (
        patch(
            "agent.intercept.extract_memory_candidates",
            return_value=[("喜欢Python", "preference")],
        ),
        patch(
            "agent.intercept.confirm_candidates_batch",
            new=AsyncMock(return_value=[("喜欢Python", "preference")]),
        ),
        patch("memory.profile.handle_intercept", new=save),
    ):
        result, saved = await apply_memory_context("profile update", hint)

    assert saved == ["工程师", "喜欢Python"]
    assert "已记录" in result.hint_text
    assert save.await_count == 2


@pytest.mark.asyncio
async def test_personal_memory_context_includes_recalled_profile():
    hint = _hint("personal_memory")
    hint.suggested_tools = ["recall_memory"]

    with (
        patch("agent.intercept.extract_memory_candidates", return_value=[]),
        patch(
            "memory.profile.search_profile",
            new=AsyncMock(return_value=[{"text": "用户名叫张三"}]),
        ),
    ):
        result, saved = await apply_memory_context("我是谁", hint)

    assert saved == []
    assert "用户名叫张三" in result.hint_text
