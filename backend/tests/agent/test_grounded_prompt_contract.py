"""Contract tests for the ReAct agent system prompt (v0.2.0)."""

from pathlib import Path


def _prompt() -> str:
    return (Path(__file__).parents[2] / "agent" / "prompts" / "system.txt").read_text(encoding="utf-8")


class TestPartialAnswerGuidance:
    def test_prompt_provides_partial_answer_strategy(self):
        prompt = _prompt()

        assert "先回答有依据的部分" in prompt
        assert "无法确认" in prompt
        assert "只要来源能支持问题的一部分" in prompt

    def test_prompt_allows_evidence_only_synthesis(self):
        prompt = _prompt()

        assert "可以把不同来源的明确事实并列展示" in prompt or "并列" in prompt
        assert "不要把不同来源的事实推导出新的共同点、差异、因果或优劣关系" in prompt


class TestCitationRequirements:
    def test_prompt_requires_claim_level_citations(self):
        prompt = _prompt()

        assert "每个事实独立引用" in prompt
        assert "成本下降了 90% [S1]" in prompt or "引用放在句号之前" in prompt
        assert "引用放在句号之前" in prompt

    def test_prompt_minimum_sufficient_citations(self):
        prompt = _prompt()

        assert "足够且最少" in prompt


class TestOutputStructure:
    def test_prompt_specifies_gfm_format(self):
        prompt = _prompt()

        assert "GitHub Flavored Markdown" in prompt
        assert "不要包裹在代码围栏中" in prompt

    def test_prompt_structure_by_query_type(self):
        prompt = _prompt()

        assert "**结论：**" in prompt
        assert "要点列表" in prompt
        assert "有序列表" in prompt
        assert "比较用分节标题" in prompt

    def test_prompt_conciseness_with_completeness(self):
        prompt = _prompt()

        assert "简洁但完整" in prompt
        assert "不要为了简洁省略关键信息" in prompt


class TestReActPrinciples:
    def test_prompt_describes_react_loop(self):
        prompt = _prompt()

        assert "ReAct" in prompt
        assert "观察" in prompt
        assert "判断" in prompt
        assert "行动" in prompt

    def test_prompt_autonomous_decision(self):
        prompt = _prompt()

        assert "自主决策" in prompt or "自主推理" in prompt

    def test_prompt_requires_sources_for_facts(self):
        prompt = _prompt()

        assert "所有事实性回答必须引用具体来源" in prompt
        assert "[S数字]" in prompt or "[S" in prompt
