"""Stable, presentation-neutral summaries for agent tool results."""

from __future__ import annotations

from typing import Any

TOOL_RESULT_KINDS = {
    "search_docs": "knowledge_results",
    "web_search": "web_results",
    "recall_memory": "memories",
    "list_documents": "documents",
    "get_document_info": "document",
    "calculator": "calculation",
}


def _non_negative_int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int) and value >= 0:
        return value
    if isinstance(value, float) and value >= 0 and value.is_integer():
        return int(value)
    return None


def result_count(data: Any) -> int | None:
    """Return a real result count, or None when the tool has no count concept.

    Explicit ``count`` is authoritative because persisted result lists may be
    truncated. List fields are only inspected when the field actually exists;
    this avoids treating a missing ``results`` field as an empty list.
    """
    if not isinstance(data, dict):
        return None

    count = _non_negative_int(data.get("count"))
    if count is not None:
        return count

    for key in ("results", "documents", "items"):
        if key in data and isinstance(data[key], list):
            return len(data[key])
    return None


def summarize_tool_result(tool_name: str, data: Any) -> dict[str, Any]:
    """Build the small result contract consumed by live and history UIs."""
    summary: dict[str, Any] = {
        "kind": TOOL_RESULT_KINDS.get(tool_name, "generic"),
        "count": result_count(data),
    }
    if not isinstance(data, dict):
        return summary

    if tool_name == "get_document_info" and data.get("filename"):
        summary["name"] = str(data["filename"])
    elif tool_name == "calculator" and "result" in data:
        value = data["result"]
        if isinstance(value, str | int | float) and not isinstance(value, bool):
            summary["value"] = value
    return summary
