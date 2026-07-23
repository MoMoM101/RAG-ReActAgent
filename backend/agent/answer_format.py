"""Deterministic, content-preserving Markdown cleanup for final answers."""

from __future__ import annotations

import re

_OUTER_MARKDOWN_FENCE_RE = re.compile(
    r"^\s*```(?:markdown|md)\s*\n(?P<body>.*?)\n```\s*$",
    re.IGNORECASE | re.DOTALL,
)
_FENCED_BLOCK_RE = re.compile(r"(```.*?```)", re.DOTALL)
_PROCESS_LEAD_RE = re.compile(
    r"^\s*(?:(?:(?:让我|我先|先|在知识库中)(?:来|去)?"
    r"(?:(?:搜索|检索|查询|查找)一下|看一下).*?[。！？!?])\s*)+",
)
_INLINE_HEADING_RE = re.compile(r"(?<!^)(?<!\n)(?<!#)(#{2,6})(?!#)[ \t]*(?=\S)")
_LINE_HEADING_RE = re.compile(r"(?m)^(\s{0,3}#{1,6})(?!#)(?=[^\s#])")
_LOOSE_BOLD_OPEN_RE = re.compile(r"\*\*\s+([^*\n]+?\S)\*\*")
_LOOSE_BOLD_RE = re.compile(r"\*\*([^*\n]*?\S)\s+\*\*")
_TABLE_ROW_RE = re.compile(r"^\s*(?:(?:[-*+]|\d+[.)、])\s+)?(\|.*)$")


def _complete_table_row(row: str) -> str:
    row = re.sub(r"[。；;]\s*$", "", row.strip())
    return row if row.endswith("|") else f"{row} |"


def _is_separator_row(row: str) -> bool:
    cells = [cell.strip() for cell in row.strip("|").split("|")]
    return bool(cells) and all(re.fullmatch(r":?-{3,}:?", cell) for cell in cells)


def _normalize_tables(text: str) -> str:
    lines = text.splitlines()
    output: list[str] = []
    index = 0
    while index < len(lines):
        match = _TABLE_ROW_RE.match(lines[index])
        if not match:
            output.append(lines[index])
            index += 1
            continue

        rows: list[str] = []
        original_rows: list[str] = []
        while index < len(lines):
            row_match = _TABLE_ROW_RE.match(lines[index])
            if not row_match:
                break
            original_rows.append(lines[index])
            rows.append(_complete_table_row(row_match.group(1)))
            index += 1

        if len(rows) < 2:
            output.extend(original_rows)
            continue

        column_count = max(1, len(rows[0].strip("|").split("|")))
        separator = "| " + " | ".join("---" for _ in range(column_count)) + " |"
        if output and output[-1].strip():
            output.append("")
        if len(rows) > 1 and _is_separator_row(rows[1]):
            output.extend(rows)
        else:
            output.extend([rows[0], separator, *rows[1:]])
        if index < len(lines) and lines[index].strip():
            output.append("")
    return "\n".join(output)


def _normalize_prose_segment(text: str, *, is_first: bool) -> str:
    if is_first:
        text = _PROCESS_LEAD_RE.sub("", text)
    text = _INLINE_HEADING_RE.sub(r"\n\n\1 ", text)
    text = _LINE_HEADING_RE.sub(r"\1 ", text)
    text = _LOOSE_BOLD_OPEN_RE.sub(r"**\1**", text)
    text = _LOOSE_BOLD_RE.sub(r"**\1**", text)
    text = re.sub(r"(?m)^\s*已确认[：:]\s*$", "**已确认：**", text)
    return _normalize_tables(text)


def normalize_answer_markdown(answer: str) -> str:
    """Repair common model Markdown mistakes without changing factual wording."""
    normalized = answer.replace("\r\n", "\n").replace("\r", "\n").strip()
    outer_fence = _OUTER_MARKDOWN_FENCE_RE.fullmatch(normalized)
    if outer_fence:
        normalized = outer_fence.group("body").strip()
    parts = _FENCED_BLOCK_RE.split(normalized)
    for index in range(0, len(parts), 2):
        parts[index] = _normalize_prose_segment(parts[index], is_first=index == 0)
    normalized = "".join(parts)
    normalized = re.sub(r"\n{3,}", "\n\n", normalized)
    return normalized.strip()
