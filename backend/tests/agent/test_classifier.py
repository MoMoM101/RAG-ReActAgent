"""意图分类规则边界测试 — 纯函数，不依赖 LLM。"""

import pytest
from agent.classifier import _rule_match


class TestAcknowledgment:
    def test_ack_with_history(self):
        for word in ["好的", "嗯", "ok", "OK", "好", "行", "可以", "明白了", "懂了", "谢谢", "感谢"]:
            result = _rule_match(word, has_history=True)
            assert result is not None, f"'{word}' should match"
            assert result.intent == "acknowledgment"

    def test_ack_without_history_returns_none(self):
        """无对话历史时确认词不匹配（用户第一句话不可能是确认）。"""
        result = _rule_match("好的", has_history=False)
        assert result is None


class TestFollowup:
    def test_short_with_pronoun(self):
        """短追问 + 指代词 → context_followup。"""
        for q in ["这个呢", "那个是什么", "他说的对吗", "这些呢", "还有吗"]:
            result = _rule_match(q, has_history=True)
            assert result is not None, f"'{q}' should match"
            assert result.intent == "context_followup"

    def test_short_without_pronoun(self):
        """短追问无指代词（≤12字符）仍匹配 context_followup。"""
        result = _rule_match("解释一下", has_history=True)
        assert result is not None
        assert result.intent == "context_followup"

    def test_followup_without_history(self):
        """无历史时追问标记不匹配。"""
        result = _rule_match("这个呢", has_history=False)
        assert result is None

    def test_medium_followup(self):
        """12 < len ≤ 30 且无指代词 → possible_followup。"""
        result = _rule_match("相关文件中记录了哪些重要信息内容", has_history=True)
        assert result is not None
        assert result.intent == "possible_followup"

    def test_long_query_not_followup(self):
        """超过 30 字符且无特殊标记 → 不匹配。"""
        result = _rule_match("量子力学中薛定谔方程的数学推导过程及其物理意义详细介绍与应用场景分析", has_history=True)
        assert result is None


class TestCalculator:
    def test_basic_arithmetic(self):
        for q in ["1+2等于多少", "计算3*4", "10除以2", "100减50"]:
            result = _rule_match(q, has_history=False)
            assert result is not None, f"'{q}' should match"
            assert result.intent == "calculation"

    def test_simple_expression(self):
        result = _rule_match("3+5", has_history=False)
        assert result is not None
        assert result.intent == "calculation"

    def test_no_math_keyword(self):
        """纯数字无计算关键词 → 不匹配。"""
        result = _rule_match("今天气温30度", has_history=False)
        assert result is None


class TestDocumentListing:
    def test_various_phrases(self):
        for q in ["有哪些文档", "文档列表", "所有文档", "什么文档", "哪些文件", "文件列表", "列出文档"]:
            result = _rule_match(q, has_history=False)
            assert result is not None, f"'{q}' should match"
            assert result.intent == "document_listing"

    def test_not_document_query(self):
        """文档相关问题但不是列表类 → 不匹配。"""
        result = _rule_match("这份文档讲了什么", has_history=False)
        assert result is None


class TestNoMatch:
    def test_general_knowledge(self):
        assert _rule_match("量子力学是什么", has_history=False) is None

    def test_greeting(self):
        assert _rule_match("你好", has_history=False) is None

    def test_empty(self):
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
    async def test_llm_classify_knowledge_retrieval(self, make_fake_llm):
        """LLM returns knowledge_retrieval intent."""
        from llm.base import LLMResponse

        make_fake_llm([
            [LLMResponse(tool_calls=[
                _make_llm_tool_call("classify_intent", {
                    "intent": "knowledge_retrieval",
                    "suggested_tools": ["search_docs"],
                    "hint_text": "search knowledge base",
                }, call_id="ci"),
            ])],
        ])

        from agent.classifier import _llm_classify
        result = await _llm_classify("machine learning basics", False)

        assert result.intent == "knowledge_retrieval"
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

    def test_classify_intent_returns_llm_needed(self):
        """Query with no rule match → intent=_llm_needed."""
        from agent.classifier import classify_intent
        result = classify_intent("量子力学的基本原理是什么", [])
        assert result.intent == "_llm_needed"
        assert result.confidence == 0.0

    @pytest.mark.asyncio
    async def test_llm_classify_falls_back_to_rules(self):
        """llm_classify with rule-matching query → returns rule result, no LLM call."""
        from agent.classifier import llm_classify
        result = await llm_classify("有哪些文档", [])
        # Should match document_listing rule, not call LLM
        assert result.intent == "document_listing"


def _make_llm_tool_call(name, args, call_id="call_1"):
    from llm.base import ToolCall
    return ToolCall(id=call_id, name=name, arguments=args)
