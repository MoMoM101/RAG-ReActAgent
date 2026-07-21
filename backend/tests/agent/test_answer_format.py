"""Regression tests for deterministic final-answer Markdown cleanup."""

from agent.answer_format import normalize_answer_markdown


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
