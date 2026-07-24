"""意图分类测试 — v0.2.0 后规则层已移除，全部走 LLM。"""

import pytest

from agent.classifier import _rule_match


class TestRuleLayerRemoved:
    """v0.2.0: rule layer removed — model autonomously decides tools via ReAct."""

    def test_all_queries_return_none(self):
        """_rule_match always returns None regardless of query type."""
        queries = [
            "好的", "嗯", "ok", "OK", "好", "行", "可以", "明白了", "谢谢",
            "这个呢", "那个是什么", "他说的对吗", "这些呢", "还有吗",
            "解释一下",
            "1+2等于多少", "计算3*4", "10除以2", "100减50", "3+5",
            "有哪些文档", "文档列表", "所有文档", "什么文档", "列出文档",
            "量子力学是什么", "你好", "今天气温30度",
        ]
        for q in queries:
            result = _rule_match(q, has_history=True)
            assert result is None, f"'{q}': rule layer removed, should always return None"

    def test_rule_layer_without_history_returns_none(self):
        assert _rule_match("好的", has_history=False) is None
        assert _rule_match("这个呢", has_history=False) is None

    def test_empty_returns_none(self):
        assert _rule_match("", has_history=False) is None


class TestLLMClassify:
    @pytest.mark.asyncio
    async def test_llm_classify_personal_memory(self, make_fake_llm):
        """LLM returns classify_intent with personal_memory + save_to_profile."""
        from llm.base import LLMResponse

        make_fake_llm([
            [LLMResponse(tool_calls=[
                _make_llm_tool_call("classify_intent", {
                    "intent": "personal_memory",
                    "suggested_tools": ["recall_memory"],
                    "hint_text": "user is sharing personal info",
                    "save_to_profile": [{"content": "用户叫小明", "type": "identity"}],
                }, call_id="ci"),
            ])],
        ])

        from agent.classifier import _llm_classify
        result = await _llm_classify("我叫小明", False)

        assert result.intent == "personal_memory"
        assert "recall_memory" in result.suggested_tools
        assert result.save_to_profile is not None
        assert len(result.save_to_profile) == 1
        assert result.save_to_profile[0]["content"] == "用户叫小明"

    @pytest.mark.asyncio
    async def test_llm_classify_general_chat(self, make_fake_llm):
        """LLM returns classify_intent with general_chat."""
        from llm.base import LLMResponse

        make_fake_llm([
            [LLMResponse(tool_calls=[
                _make_llm_tool_call("classify_intent", {
                    "intent": "general_chat",
                    "suggested_tools": ["search_docs"],
                    "hint_text": "search knowledge base",
                }, call_id="ci"),
            ])],
        ])

        from agent.classifier import _llm_classify
        result = await _llm_classify("machine learning basics", False)

        assert result.intent == "general_chat"
        assert "search_docs" in result.suggested_tools

    @pytest.mark.asyncio
    async def test_llm_classify_no_tool_call(self, make_fake_llm):
        """LLM returns text only → falls back to general_chat."""
        from llm.base import LLMResponse

        make_fake_llm([
            [LLMResponse(content="this is a chat response, no tool call")],
        ])

        from agent.classifier import _llm_classify
        result = await _llm_classify("hello", False)

        assert result.intent == "general_chat"
        assert result.confidence == 0.3

    def test_classify_intent_always_returns_llm_needed(self):
        """v0.2.0: rule layer removed, classify_intent always returns _llm_needed."""
        from agent.classifier import classify_intent
        result = classify_intent("量子力学的基本原理是什么", [])
        assert result.intent == "_llm_needed"
        assert result.confidence == 0.0

        # Even queries that used to match rules now return _llm_needed
        result = classify_intent("有哪些文档", [])
        assert result.intent == "_llm_needed"

    @pytest.mark.asyncio
    async def test_llm_classify_all_queries_go_through_llm(self, make_fake_llm):
        """v0.2.0: all queries go through LLM since rule layer is removed."""
        from llm.base import LLMResponse

        make_fake_llm([
            [LLMResponse(tool_calls=[
                _make_llm_tool_call("classify_intent", {
                    "intent": "general_chat",
                    "suggested_tools": ["search_docs"],
                    "hint_text": "list documents",
                }, call_id="ci"),
            ])],
        ])

        from agent.classifier import llm_classify
        result = await llm_classify("有哪些文档", [])
        # Now goes through LLM, returns LLM's classification
        assert result.intent == "general_chat"


def _make_llm_tool_call(name, args, call_id="call_1"):
    from llm.base import ToolCall
    return ToolCall(id=call_id, name=name, arguments=args)
