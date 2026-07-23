"""记忆拦截测试 — 正则提取 + 噪音过滤。"""

import pytest

from agent.intercept import extract_memory_candidate, extract_memory_candidates
from llm.base import LLMResponse


class TestIdentityExtraction:
    def test_wo_jiao(self):
        candidates = extract_memory_candidates("我叫张三")
        assert ("用户叫张三", "identity") in candidates

    def test_bare_wo_shi_not_extracted_by_regex(self):
        """Bare '我是X' is too ambiguous for regex — handled by classifier LLM."""
        candidates = extract_memory_candidates("我是软件工程师")
        assert len(candidates) == 0  # no longer extracted by regex

    def test_explicit_role_marker(self):
        """'我职业是X' with explicit marker is unambiguous and still extracted."""
        candidates = extract_memory_candidates("我职业是软件工程师")
        assert ("用户是软件工程师", "identity") in candidates

    def test_wo_jiao_with_punctuation(self):
        candidates = extract_memory_candidates("我叫张三，今年30岁")
        assert ("用户叫张三", "identity") in candidates

    def test_wo_shi_with_period(self):
        """Bare '我是X' not extracted. Period-delimited case covered by classifier."""
        candidates = extract_memory_candidates("我是后端开发。")
        assert len(candidates) == 0


class TestPreferenceExtraction:
    def test_wo_xihuan(self):
        candidates = extract_memory_candidates("我喜欢Python")
        assert ("用户喜欢Python", "preference") in candidates

    def test_wo_ai(self):
        candidates = extract_memory_candidates("我爱喝咖啡")
        assert ("用户喜欢喝咖啡", "preference") in candidates

    def test_wo_xiguan(self):
        candidates = extract_memory_candidates("我习惯早起")
        assert ("用户习惯早起", "preference") in candidates


class TestDecisionExtraction:
    def test_wo_jueding(self):
        candidates = extract_memory_candidates("我决定用FastAPI做后端")
        assert ("用户决定用FastAPI做后端", "decision") in candidates


class TestFactExtraction:
    def test_wo_xiangmu(self):
        candidates = extract_memory_candidates("我项目是RAG系统")
        assert ("用户RAG系统（项目/当前工作）", "fact") in candidates


class TestNoiseFiltering:
    def test_noise_word_waimai(self):
        """'我叫外卖' 不是身份声明。"""
        candidates = extract_memory_candidates("我叫外卖")
        assert len(candidates) == 0

    def test_noise_word_kuaidi(self):
        candidates = extract_memory_candidates("我叫快递")
        assert len(candidates) == 0

    def test_noise_word_dianhua(self):
        candidates = extract_memory_candidates("我叫电话")
        assert len(candidates) == 0

    def test_noise_word_shuo(self):
        """'我喜欢说唱' → '说' 是噪音词，但 '说唱' 不是噪音词..."""
        candidates = extract_memory_candidates("我喜欢说")
        assert len(candidates) == 0  # "说" 是噪音


class TestLengthLimits:
    def test_too_long(self):
        """超过 80 字符的值不提取。"""
        candidates = extract_memory_candidates("我叫" + "张" * 81)
        assert len(candidates) == 0

    def test_single_char(self):
        """单字符不提取（≥1 但通常无意义）。"""
        candidates = extract_memory_candidates("我喜欢A")
        assert len(candidates) == 1  # 1 字符在允许范围内


class TestMultipleCandidates:
    def test_two_in_one_message(self):
        candidates = extract_memory_candidates("我叫张三，我喜欢Python")
        assert len(candidates) == 2
        types = {t for _, t in candidates}
        assert "identity" in types
        assert "preference" in types

    def test_three_in_one_message(self):
        """Name + role (with explicit marker) + decision in one message."""
        candidates = extract_memory_candidates("我叫李四，我职业是前端工程师，我决定用React")
        assert len(candidates) == 3

    def test_name_and_role_compound(self):
        """Name + explicit role marker gives two identity candidates."""
        candidates = extract_memory_candidates("我叫李四，我工作是后端开发")
        assert len(candidates) == 2
        types = {t for _, t in candidates}
        assert types == {"identity"}

    def test_dedup_same_type(self):
        """同一句式不重复匹配（'我叫XX' 只匹配一次）。"""
        candidates = extract_memory_candidates("我叫张三也叫李四")
        # 第二个 "叫" 匹配到 "李四" 而非再次匹配 "张三也叫李四"
        assert len(candidates) >= 1


class TestBackwardCompatAlias:
    def test_extract_memory_candidate_returns_first(self):
        result = extract_memory_candidate("我叫张三，我喜欢Python")
        assert result is not None
        assert result[1] == "identity"

    def test_extract_memory_candidate_returns_none(self):
        result = extract_memory_candidate("今天天气不错")
        assert result is None


class TestConfirmMemory:
    @pytest.mark.asyncio
    async def test_confirm_memory_save_true(self, make_fake_llm):
        """LLM returns decide_memory with save=True → returns True."""
        make_fake_llm([
            [LLMResponse(tool_calls=[
                _make_tool_call("decide_memory", {"save": True}, call_id="c1"),
            ])],
        ])

        from agent.intercept import confirm_memory
        result = await confirm_memory("用户是软件工程师")
        assert result is True

    @pytest.mark.asyncio
    async def test_confirm_memory_save_false(self, make_fake_llm):
        """LLM returns decide_memory with save=False → returns False."""
        make_fake_llm([
            [LLMResponse(tool_calls=[
                _make_tool_call("decide_memory", {"save": False}, call_id="c1"),
            ])],
        ])

        from agent.intercept import confirm_memory
        result = await confirm_memory("用户叫了外卖")
        assert result is False

    @pytest.mark.asyncio
    async def test_confirm_memory_no_tool_call(self, make_fake_llm):
        """LLM returns text only, no tool call → default False."""
        make_fake_llm([
            [LLMResponse(content="I don't know what to save")],
        ])

        from agent.intercept import confirm_memory
        result = await confirm_memory("some random text")
        assert result is False


class TestConfirmCandidatesBatch:
    @pytest.mark.asyncio
    async def test_batch_confirms_subset(self, make_fake_llm):
        """3 candidates, LLM returns save_indices=[1,3] → returns items 0 and 2."""
        make_fake_llm([
            [LLMResponse(tool_calls=[
                _make_tool_call("decide_memories", {"save_indices": [1, 3]}, call_id="c1"),
            ])],
        ])

        candidates = [
            ("用户叫张三", "identity"),
            ("用户喜欢外卖", "preference"),
            ("用户决定用FastAPI", "decision"),
        ]

        from agent.intercept import confirm_candidates_batch
        result = await confirm_candidates_batch(candidates)

        assert len(result) == 2
        assert result[0][0] == "用户叫张三"
        assert result[1][0] == "用户决定用FastAPI"

    @pytest.mark.asyncio
    async def test_batch_single_falls_back_to_confirm_memory(self, make_fake_llm):
        """Single candidate → uses confirm_memory (decide_memory tool)."""
        make_fake_llm([
            [LLMResponse(tool_calls=[
                _make_tool_call("decide_memory", {"save": True}, call_id="c1"),
            ])],
        ])

        candidates = [("用户是工程师", "identity")]

        from agent.intercept import confirm_candidates_batch
        result = await confirm_candidates_batch(candidates)

        assert len(result) == 1

    @pytest.mark.asyncio
    async def test_batch_empty_returns_empty(self):
        """Empty list → []."""
        from agent.intercept import confirm_candidates_batch
        result = await confirm_candidates_batch([])
        assert result == []

    @pytest.mark.asyncio
    async def test_batch_invalid_indices_filtered(self, make_fake_llm):
        """LLM returns out-of-range index → filtered out."""
        make_fake_llm([
            [LLMResponse(tool_calls=[
                _make_tool_call("decide_memories", {"save_indices": [1, 5]}, call_id="c1"),
            ])],
        ])

        candidates = [
            ("用户叫张三", "identity"),
            ("用户喜欢Python", "preference"),
        ]

        from agent.intercept import confirm_candidates_batch
        result = await confirm_candidates_batch(candidates)

        assert len(result) == 1
        assert result[0][0] == "用户叫张三"


def _make_tool_call(name, args, call_id="call_1"):
    from llm.base import ToolCall
    return ToolCall(id=call_id, name=name, arguments=args)
