"""Deterministic, content-preserving Markdown cleanup for final answers."""

from __future__ import annotations

import re

_FENCED_BLOCK_RE = re.compile(r"(```.*?```)", re.DOTALL)
_PROCESS_LEAD_RE = re.compile(
    r"^\s*(?:(?:(?:让我|我先|先|在知识库中)(?:来|去)?"
    r"(?:(?:搜索|检索|查询|查找)一下|看一下).*?[。！？!?])\s*)+",
)
_INLINE_HEADING_RE = re.compile(r"(?<!^)(?<!\n)(?<!#)(#{1,6}\s+)")
_LOOSE_BOLD_RE = re.compile(r"\*\*([^*\n]*?\S)\s+\*\*")
_BULLET_TABLE_ROW_RE = re.compile(r"^\s*[-*+]\s+(\|.*)$")


def _complete_table_row(row: str) -> str:
    row = re.sub(r"[。；;]\s*$", "", row.strip())
    return row if row.endswith("|") else f"{row} |"


def _normalize_bullet_tables(text: str) -> str:
    lines = text.splitlines()
    output: list[str] = []
    index = 0
    while index < len(lines):
        match = _BULLET_TABLE_ROW_RE.match(lines[index])
        if not match:
            output.append(lines[index])
            index += 1
            continue

        rows: list[str] = []
        while index < len(lines):
            row_match = _BULLET_TABLE_ROW_RE.match(lines[index])
            if not row_match:
                break
            rows.append(_complete_table_row(row_match.group(1)))
            index += 1

        if len(rows) < 2:
            output.extend(f"- {row}" for row in rows)
            continue

        column_count = max(1, len(rows[0].strip("|").split("|")))
        separator = "| " + " | ".join("---" for _ in range(column_count)) + " |"
        if output and output[-1].strip():
            output.append("")
        output.extend([rows[0], separator, *rows[1:]])
        if index < len(lines) and lines[index].strip():
            output.append("")
    return "\n".join(output)


def _normalize_prose_segment(text: str, *, is_first: bool) -> str:
    if is_first:
        text = _PROCESS_LEAD_RE.sub("", text)
    text = _INLINE_HEADING_RE.sub(r"\n\n\1", text)
    text = _LOOSE_BOLD_RE.sub(r"**\1**", text)
    text = re.sub(r"(?m)^\s*已确认[：:]\s*$", "**已确认：**", text)
    return _normalize_bullet_tables(text)


def normalize_answer_markdown(answer: str) -> str:
    """Repair common model Markdown mistakes without changing factual wording."""
    normalized = answer.replace("\r\n", "\n").replace("\r", "\n").strip()
    parts = _FENCED_BLOCK_RE.split(normalized)
    for index in range(0, len(parts), 2):
        parts[index] = _normalize_prose_segment(parts[index], is_first=index == 0)
    normalized = "".join(parts)
    normalized = re.sub(r"\n{3,}", "\n\n", normalized)
    return normalized.strip()
