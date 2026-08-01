"""Regression matrix for multi-turn query semantics."""

import pytest

from agent.query_semantics import (
    extract_comparison_entities,
    is_comparison_query,
    is_conditional_decision_query,
    requires_calculator_tool,
    requires_whole_answer_validation,
    resolve_followup_query,
    sanitize_conversation_history,
)
from llm.base import ChatMessage, ToolCall


def _history(*messages: str) -> list[ChatMessage]:
    return [ChatMessage(role="user", content=message) for message in messages]


def test_resolves_singular_reference_from_latest_topic():
    assert resolve_followup_query("MCP 和它有什么区别", _history("Skill 是什么")) == "MCP 和Skill有什么区别"


def test_resolves_plural_and_ordered_references_from_previous_pair():
    history = _history("MCP 和 Skill 有什么区别")

    assert resolve_followup_query("它们各适合什么场景", history) == "MCP和Skill各适合什么场景"
    assert resolve_followup_query("前者有什么优势", history) == "MCP有什么优势"
    assert resolve_followup_query("后者有什么优势", history) == "Skill有什么优势"


def test_plural_reference_without_pair_is_not_malformed():
    assert resolve_followup_query("它们有什么区别", _history("Skill 是什么")) == "它们有什么区别"


def test_underspecified_followup_inherits_simple_topic():
    assert resolve_followup_query("详细说说", _history("介绍一下 Skill")) == "Skill的详细说明"


def test_resolves_leading_comparison_with_omitted_subject():
    assert resolve_followup_query("和mcp有什么区别", _history("skill是什么")) == "skill和mcp有什么区别"


@pytest.mark.parametrize(
    "query",
    [
        "MCP vs Skill",
        "MCP 相比 Skill 有什么优势",
        "MCP 相较于 Skill",
        "MCP 跟 Skill 怎么选",
        "MCP 和 Skill 哪个好",
    ],
)
def test_comparison_aliases_require_whole_answer_validation(query: str):
    assert is_comparison_query(query)
    assert requires_whole_answer_validation(query)


@pytest.mark.parametrize("query", ["为什么失败", "它如何影响结果", "每层职责是什么", "F1 怎么计算"])
def test_relation_queries_require_whole_answer_validation(query: str):
    assert requires_whole_answer_validation(query)


@pytest.mark.parametrize(
    "query",
    ["这段中断是否必然计入 SLA？", "维护窗口内是否自动排除？", "是否一定算作不可用？"],
)
def test_conditional_decisions_require_whole_answer_validation(query: str):
    assert is_conditional_decision_query(query)
    assert requires_whole_answer_validation(query)


@pytest.mark.parametrize(
    "query",
    ["请计算 75 个节点的首年预算", "1 台设备的总额是多少", "10% 折后金额是多少"],
)
def test_numeric_amount_queries_require_calculator(query: str):
    assert requires_calculator_tool(query)


def test_formula_explanation_without_numeric_inputs_does_not_require_calculator():
    assert not requires_calculator_tool("月度可用性的计算公式是什么？")


@pytest.mark.parametrize(
    "query",
    ["请根据价格表计算总预算", "这些项目的合计是多少？", "Calculate the total budget from the pricing table."],
)
def test_explicit_amount_calculation_without_query_numbers_requires_calculator(query: str):
    assert requires_calculator_tool(query)


def test_general_budget_description_without_numbers_does_not_require_calculator():
    assert not requires_calculator_tool("请介绍预算管理流程")


@pytest.mark.parametrize("query", ["什么是 Skill", "介绍一下 MCP", "详细说明 RAG"])
def test_coverage_queries_require_whole_answer_validation(query: str):
    assert requires_whole_answer_validation(query)


@pytest.mark.parametrize(
    "query",
    ["Skill", "列出主要功能", "总结这份资料", "如何部署项目", "安装步骤", "使用指南"],
)
def test_structured_output_queries_require_whole_answer_validation(query: str):
    assert requires_whole_answer_validation(query)


@pytest.mark.parametrize("query", ["光伏成本如何？", "光伏成本下降了吗？"])
def test_short_questions_are_not_mistaken_for_term_only_overviews(query: str):
    assert not requires_whole_answer_validation(query)


def test_extracts_explicit_comparison_entities():
    assert extract_comparison_entities("MCP 和 Skill 有什么区别") == ("MCP", "Skill")
    assert extract_comparison_entities("MCP vs Skill") == ("MCP", "Skill")
    assert extract_comparison_entities("MCP和Skill怎么选") == ("MCP", "Skill")
    assert extract_comparison_entities("MCP相比Skill有什么优势") == ("MCP", "Skill")
    assert extract_comparison_entities("MCP和Skill各自适合什么场景") == ("MCP", "Skill")


def test_sanitize_history_removes_stale_tools_and_citations():
    history = [
        ChatMessage(role="user", content="Skill 是什么"),
        ChatMessage(role="tool", content='{"sources": []}', tool_name="search_docs"),
        ChatMessage(
            role="assistant",
            content="Skill 是能力模块 [S1]。",
            tool_calls=[ToolCall(id="call-1", name="search_docs", arguments={"query": "Skill"})],
        ),
    ]

    sanitized = sanitize_conversation_history(history)

    assert [message.role for message in sanitized] == ["user", "assistant"]
    assert sanitized[1].content == "Skill 是能力模块。"
    assert sanitized[1].tool_calls is None


def test_sanitize_history_removes_stale_web_citations():
    history = [
        ChatMessage(role="user", content="发布年份是什么"),
        ChatMessage(role="assistant", content="该版本发布于 2026 年 [WS3]。"),
    ]

    sanitized = sanitize_conversation_history(history)

    assert sanitized[1].content == "该版本发布于 2026 年。"
