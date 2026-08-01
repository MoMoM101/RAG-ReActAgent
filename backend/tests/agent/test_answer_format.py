"""Regression tests for deterministic final-answer Markdown cleanup."""

import re

from agent.answer_format import (
    ensure_document_inventory_section,
    normalize_answer_markdown,
)


def _inventory_source():
    return {
        "citation_id": "S1",
        "chunk_id": "tool:list_documents",
        "source_type": "tool",
        "documents": [
            {
                "filename": "01_product.md",
                "file_type": ".md",
                "status": "ready",
            },
            {
                "filename": "02_pricing.xlsx",
                "file_type": ".xlsx",
                "status": "ready",
            },
        ],
    }


def test_document_list_request_gets_a_dedicated_inventory_section():
    answer = "- **产品规格**：在 01_product.md 中。\n- **价格**：在 02_pricing.xlsx 中。"

    completed = ensure_document_inventory_section(
        "请列出当前知识库中的文档，并说明哪些文档包含产品规格和价格。",
        answer,
        [_inventory_source()],
    )

    assert completed.startswith("### 文档列表")
    assert "`01_product.md`（类型：.md；状态：ready） [S1]。" in completed
    assert "`02_pricing.xlsx`（类型：.xlsx；状态：ready） [S1]。" in completed
    assert "### 内容对应" in completed
    assert completed.endswith(answer)


def test_existing_document_list_section_is_not_duplicated():
    answer = "### 文档列表\n\n- `01_product.md` [S1]。"

    completed = ensure_document_inventory_section(
        "请列出当前知识库中的文档。",
        answer,
        [_inventory_source()],
    )

    assert completed == answer


def test_removes_search_narration_and_separates_glued_heading():
    answer = "在知识库中搜索一下相关定义。### Skill 是什么\n\nSkill 是能力扩展单元 [S1]。"

    normalized = normalize_answer_markdown(answer)

    assert normalized.startswith("### Skill 是什么")
    assert "搜索一下" not in normalized


def test_repairs_real_bullet_wrapped_markdown_table_shape():
    answer = """先搜索一下知识库中关于 Skill 与 MCP 对比的相关内容。已确认：
- **核心价值与适用场景 ** [S4]。
- | 对比维度 | MCP | Agent Skills | [S1, S2]。
- | 核心价值 | 协议层标准化 | 应用层封装 | [S4]。
- | 扩展方式 | MCP Server | Skill 文件夹 | [S3]。"""

    normalized = normalize_answer_markdown(answer)

    assert normalized.startswith("**已确认：**")
    assert "**核心价值与适用场景**" in normalized
    assert not any(line.startswith("- |") for line in normalized.splitlines())
    assert "\n\n| 对比维度" in normalized
    assert "| 对比维度 | MCP | Agent Skills | [S1, S2] |" in normalized
    assert "| --- | --- | --- | --- |" in normalized


def test_does_not_rewrite_markdown_like_text_inside_fenced_code():
    answer = "示例：\n```markdown\n- | 不是 | 表格 |\n### 原样标题\n```"

    normalized = normalize_answer_markdown(answer)

    assert "```markdown\n- | 不是 | 表格 |\n### 原样标题\n```" in normalized


def test_keeps_factual_step_that_starts_with_search_wording():
    answer = "先搜索向量索引，再执行重排 [S1]。"

    assert normalize_answer_markdown(answer) == answer


def test_unwraps_whole_answer_markdown_fence_and_repairs_heading_spacing():
    answer = "```markdown\n###Skill\n\n- 能力扩展 [S1]。\n```"

    normalized = normalize_answer_markdown(answer)

    assert normalized == "### Skill\n\n- 能力扩展 [S1]。"


def test_inserts_missing_separator_for_plain_or_numbered_table_rows():
    plain = "| 维度 | MCP | Skill |\n| 定位 | 连接层 | 应用层 |"
    numbered = "1. | 维度 | MCP | Skill |\n2. | 定位 | 连接层 | 应用层 |"

    for answer in (plain, numbered):
        normalized = normalize_answer_markdown(answer)
        assert "| --- | --- | --- |" in normalized
        assert not any(re.match(r"^\s*\d+[.)、]\s+\|", line) for line in normalized.splitlines())


def test_keeps_existing_valid_table_separator_once():
    answer = "| 维度 | MCP |\n| --- | --- |\n| 定位 | 连接层 |"

    normalized = normalize_answer_markdown(answer)

    assert normalized.count("| --- | --- |") == 1


def test_unwraps_unlabeled_fence_containing_only_chinese_prose():
    answer = (
        "最终总预算如下：\n"
        "```总预算 = 硬件价格 + 平台订阅费 + 实施服务费 = 43032 元。\n\n"
        "综上所述，客户的未税首年总预算为 43032 元。```"
    )

    normalized = normalize_answer_markdown(answer)

    assert "```" not in normalized
    assert "总预算 = 硬件价格 + 平台订阅费 + 实施服务费 = 43032 元。" in normalized
    assert "综上所述，客户的未税首年总预算为 43032 元。" in normalized
