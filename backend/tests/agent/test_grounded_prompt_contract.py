"""Contract tests for partial-answer-first grounded generation."""

from pathlib import Path


def _prompt() -> str:
    return (
        Path(__file__).parents[2] / "agent" / "prompts" / "system.txt"
    ).read_text(encoding="utf-8")


def test_prompt_answers_supported_parts_before_declaring_limits():
    prompt = _prompt()

    assert "先回答所有有依据的部分" in prompt
    assert "其余内容单独写“无法确认：……”" in prompt
    assert "不要因为一个子问题无法确认而拒绝回答其他有依据的事实" in prompt


def test_prompt_requires_claim_level_citations_before_punctuation():
    prompt = _prompt()

    assert "每个事实句或列表项独立引用" in prompt
    assert "光伏成本下降了 90% [S1]。" in prompt


def test_prompt_prioritizes_coverage_without_unbounded_answers():
    prompt = _prompt()

    assert "不使用“根据检索资料”“根据来源”等固定开场" in prompt
    assert "覆盖检索结果中与问题直接相关且互不重复" in prompt
    assert "完整性优先于机械压缩" in prompt
    assert "500 个中文字符或 6 个列表项以内" in prompt
    assert "只输入一个术语或实体名称" in prompt
    assert "简洁”不等于省略关键事实" in prompt
    assert "保留这些名称，不要只改写成泛称" in prompt


def test_prompt_requires_visible_markdown_structure_by_query_type():
    prompt = _prompt()

    assert "GitHub Flavored Markdown" in prompt
    assert "不要把整段答案包在 `markdown` 代码围栏中" in prompt
    assert "单一事实使用 `**结论：**`" in prompt
    assert "定义、概览和详细说明使用 `### 主题` 后接要点列表" in prompt
    assert "步骤和操作方法使用有序列表" in prompt
    assert "不要输出空标题、空列表或只有标题没有正文" in prompt


def test_prompt_allows_evidence_only_cross_source_synthesis():
    prompt = _prompt()

    assert "可以把不同来源的明确事实并列展示" in prompt
    assert "不能从并列事实推导新的共同点、差异、因果、优劣" in prompt
    assert "`### A`、`### B`、必要时 `### 无法确认`" in prompt
    assert "每个列表项只写一个来源能完整支持的事实并独立引用" in prompt
