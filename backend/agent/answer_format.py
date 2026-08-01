"""Deterministic, content-preserving Markdown cleanup for final answers."""

from __future__ import annotations

import re
from collections.abc import Sequence
from typing import Any

_OUTER_MARKDOWN_FENCE_RE = re.compile(
    r"^\s*```(?:markdown|md)\s*\n(?P<body>.*?)\n```\s*$",
    re.IGNORECASE | re.DOTALL,
)
_FENCED_BLOCK_RE = re.compile(r"(```.*?```)", re.DOTALL)
_UNLABELED_FENCE_RE = re.compile(
    r"^```[ \t]*\n?(?P<body>.*?)\n?```$",
    re.DOTALL,
)
_CODE_LINE_RE = re.compile(
    r"(?m)^\s*(?:[$>#]|def\s|class\s|import\s|from\s+\S+\s+import\s|"
    r"const\s|let\s|var\s|function\s|SELECT\s|INSERT\s|UPDATE\s|DELETE\s)",
    re.IGNORECASE,
)
_DOCUMENT_LIST_QUERY_RE = re.compile(
    r"(?:列出|展示|显示|有哪些|什么文档|文档列表).{0,12}(?:知识库|文档)"
    r"|(?:知识库|当前).{0,12}(?:有哪些|什么|列出).{0,8}文档",
)
_DOCUMENT_LIST_HEADING_RE = re.compile(
    r"(?mi)^\s*(?:#{1,6}\s*|\*\*)文档列表(?:\*\*)?\s*[：:]?\s*$",
)
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


def _unwrap_misclassified_prose_fence(block: str) -> str:
    """Unwrap an unlabeled fence when it contains prose rather than code."""
    match = _UNLABELED_FENCE_RE.fullmatch(block)
    if not match:
        return block
    body = match.group("body").strip()
    if (
        re.search(r"[\u4e00-\u9fff]", body)
        and re.search(r"[。！？]", body)
        and not _CODE_LINE_RE.search(body)
        and not re.search(r"[{};]", body)
    ):
        return f"\n\n{body}\n\n"
    return block


def normalize_answer_markdown(answer: str) -> str:
    """Repair common model Markdown mistakes without changing factual wording."""
    normalized = answer.replace("\r\n", "\n").replace("\r", "\n").strip()
    outer_fence = _OUTER_MARKDOWN_FENCE_RE.fullmatch(normalized)
    if outer_fence:
        normalized = outer_fence.group("body").strip()
    parts = _FENCED_BLOCK_RE.split(normalized)
    for index in range(0, len(parts), 2):
        parts[index] = _normalize_prose_segment(parts[index], is_first=index == 0)
    for index in range(1, len(parts), 2):
        parts[index] = _unwrap_misclassified_prose_fence(parts[index])
    normalized = "".join(parts)
    normalized = re.sub(r"\n{3,}", "\n\n", normalized)
    return normalized.strip()


def ensure_document_inventory_section(
    query: str,
    answer: str,
    sources: Sequence[dict[str, Any]],
) -> str:
    """Prepend an authoritative inventory when a list request buried it."""
    if (
        not _DOCUMENT_LIST_QUERY_RE.search(query)
        or _DOCUMENT_LIST_HEADING_RE.search(answer)
    ):
        return answer

    inventory = next(
        (
            source
            for source in sources
            if source.get("source_type") == "tool"
            and source.get("chunk_id") == "tool:list_documents"
            and isinstance(source.get("documents"), list)
        ),
        None,
    )
    if inventory is None:
        return answer
    documents = [
        document
        for document in inventory["documents"]
        if isinstance(document, dict) and document.get("filename")
    ]
    if not documents:
        return answer

    citation_id = str(inventory.get("citation_id", ""))
    citation = f" [{citation_id}]" if citation_id else ""
    lines = [
        "### 文档列表",
        "",
        f"当前知识库共有 {len(documents)} 份文档{citation}。",
        "",
    ]
    for document in documents:
        filename = str(document.get("filename", ""))
        file_type = str(document.get("file_type", "")) or "未知类型"
        status = str(document.get("status", "")) or "未知状态"
        lines.append(
            f"- `{filename}`（类型：{file_type}；状态：{status}）{citation}。"
        )

    lines.extend(["", "### 内容对应", "", answer])
    return "\n".join(lines).strip()
