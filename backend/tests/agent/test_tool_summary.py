"""Tests for stable tool-result summaries used by the UI."""

from agent.tool_summary import result_count, summarize_tool_result


def test_list_documents_uses_explicit_count_without_false_zero():
    data = {
        "count": 2,
        "documents": [{"filename": "one.docx"}, {"filename": "two.pdf"}],
    }

    assert result_count(data) == 2
    assert summarize_tool_result("list_documents", data) == {
        "kind": "documents",
        "count": 2,
    }


def test_count_falls_back_only_to_fields_that_exist():
    assert result_count({"documents": [{}, {}]}) == 2
    assert result_count({"results": []}) == 0
    assert result_count({"result": 4}) is None
    assert result_count(None) is None


def test_explicit_count_survives_persisted_list_truncation():
    assert result_count({"count": 8, "results": [{}, {}, {}]}) == 8


def test_non_count_tools_include_useful_details():
    assert summarize_tool_result("get_document_info", {"filename": "guide.pdf"}) == {
        "kind": "document",
        "count": None,
        "name": "guide.pdf",
    }
    assert summarize_tool_result("calculator", {"result": 42}) == {
        "kind": "calculation",
        "count": None,
        "value": 42,
    }
