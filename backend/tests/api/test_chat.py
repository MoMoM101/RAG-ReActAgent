"""Test tool result persistence in chat API."""
import json
from unittest.mock import MagicMock

from api.chat import _tool_message_content, _truncate_tool_result

# ── _tool_message_content ──────────────────────────

def test_tool_message_content_uses_tool_result_json():
    """When tool_result_json is present, it should be used with a prefix."""
    m = MagicMock()
    m.tool_result_json = '{"results": [{"text": "relevant content"}]}'
    m.tool_name = "search_docs"
    m.content = "Success: 8 results"
    result = _tool_message_content(m)
    assert "relevant content" in result
    assert "[历史工具结果: search_docs]" in result
    assert "Success: 8 results" not in result


def test_tool_message_content_falls_back_to_content():
    """When tool_result_json is None, use content as-is."""
    m = MagicMock()
    m.tool_result_json = None
    m.content = "Success: 5 results"
    result = _tool_message_content(m)
    assert result == "Success: 5 results"


def test_tool_message_content_empty_content():
    """When both tool_result_json and content are empty, return empty string."""
    m = MagicMock()
    m.tool_result_json = None
    m.content = None
    result = _tool_message_content(m)
    assert result == ""


# ── _truncate_tool_result ──────────────────────────

def test_truncate_tool_result_empty():
    assert _truncate_tool_result(None) == "{}"
    assert _truncate_tool_result({}) == "{}"


def test_truncate_tool_result_keeps_calculator():
    data = {"expression": "2+2", "result": 4}
    result = _truncate_tool_result(data)
    parsed = json.loads(result)
    assert parsed["expression"] == "2+2"
    assert parsed["result"] == 4


def test_truncate_tool_result_truncates_results_to_top_3():
    data = {
        "results": [
            {"text": "a" * 500, "document_id": "d1", "score": 0.9},
            {"text": "b" * 500, "document_id": "d2", "score": 0.8},
            {"text": "c" * 500, "document_id": "d3", "score": 0.7},
            {"text": "d" * 500, "document_id": "d4", "score": 0.6},
            {"text": "e" * 500, "document_id": "d5", "score": 0.5},
        ]
    }
    result = _truncate_tool_result(data)
    parsed = json.loads(result)
    assert len(parsed["results"]) == 3
    assert parsed["results"][0]["document_id"] == "d1"


def test_truncate_tool_result_truncates_text_length():
    data = {
        "results": [
            {"text": "x" * 500, "document_id": "d1", "score": 0.9},
        ]
    }
    result = _truncate_tool_result(data)
    parsed = json.loads(result)
    assert len(parsed["results"][0]["text"]) <= 300


def test_truncate_tool_result_caps_total_size():
    """Very large result data should not exceed max_chars."""
    data = {
        "results": [
            {"text": "z" * 5000, "document_id": "d1", "score": 0.9},
            {"text": "z" * 5000, "document_id": "d2", "score": 0.8},
            {"text": "z" * 5000, "document_id": "d3", "score": 0.7},
        ]
    }
    result = _truncate_tool_result(data)
    assert len(result) <= 4003  # max_chars(4000) + "..." = 4003
