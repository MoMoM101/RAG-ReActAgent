"""Model-aware request token counting tests."""

from unittest.mock import patch

from agent.context import ContextManager
from agent.token_counter import TiktokenCounter, Utf8ByteCounter, count_message, count_tools
from llm.base import ChatMessage, ToolCall


def test_message_count_includes_tool_call_arguments():
    counter = TiktokenCounter("gpt-4o")
    plain = ChatMessage(role="assistant", content="")
    with_call = ChatMessage(
        role="assistant",
        content="",
        tool_calls=[ToolCall(id="call-1", name="search_docs", arguments={"query": "中文合同"})],
    )

    assert count_message(counter, with_call) > count_message(counter, plain)


def test_tool_schema_consumes_request_budget():
    counter = TiktokenCounter("gpt-4o")
    tools = [{"type": "function", "function": {"name": "search_docs", "description": "Search documents"}}]

    assert count_tools(counter, tools) > 4


def test_tiktoken_failure_uses_conservative_offline_fallback():
    with patch("agent.token_counter.tiktoken.get_encoding", side_effect=OSError("offline")) as get_encoding:
        counter = TiktokenCounter("gpt-4o")
        get_encoding.assert_not_called()
        measured = counter.count_text("中文 and English")

    assert counter.name == "heuristic:utf8-bytes"
    assert measured >= len("中文 and English")

    truncated = counter.truncate_text("中文 and English" * 20, 30)
    assert counter.count_text(truncated) <= 30


def test_utf8_fallback_truncation_respects_tiny_budgets():
    counter = Utf8ByteCounter()

    assert counter.count_text(counter.truncate_text("很长的文本", 2)) <= 2


def test_last_user_message_is_charged_once():
    counter = TiktokenCounter("gpt-4o")
    message = ChatMessage(role="user", content="latest question")
    manager = ContextManager(max_tokens=1000, counter=counter)

    trimmed, _, _ = manager.trim_messages([message])

    assert manager.count_request(trimmed) == count_message(counter, message)


def test_tool_result_is_really_truncated_before_sending():
    counter = TiktokenCounter("gpt-4o")
    manager = ContextManager(max_tokens=1000, tool_result_max_tokens=20, counter=counter)
    original = ChatMessage(role="tool", content="very long result " * 100, tool_call_id="call-1")

    trimmed, _, _ = manager.trim_messages([
        ChatMessage(role="user", content="question"),
        original,
    ])
    sent_tool = next(message for message in trimmed if message.role == "tool")

    assert sent_tool.content != original.content
    assert sent_tool.content.endswith("…[截断]")
    assert counter.count_text(sent_tool.content) <= 20
    assert original.content == "very long result " * 100


def test_input_budget_reserves_output_reasoning_and_safety_tokens():
    manager = ContextManager(
        max_tokens=10000,
        output_reserve=1000,
        reasoning_reserve=500,
        safety_tokens=500,
    )

    assert manager.input_budget() == 8000
    assert manager.input_budget(0.5) == 4000


def test_wrapped_tool_truncation_preserves_untrusted_content_boundary():
    counter = TiktokenCounter("gpt-4o")
    manager = ContextManager(max_tokens=1000, tool_result_max_tokens=40, counter=counter)
    closing_tag = "\n</UNTRUSTED_RETRIEVED_CONTENT>"
    wrapped = "<UNTRUSTED_RETRIEVED_CONTENT>\n" + ("unsafe data " * 100) + closing_tag

    trimmed, _, _ = manager.trim_messages([
        ChatMessage(role="user", content="question"),
        ChatMessage(role="tool", content=wrapped, tool_call_id="call-1"),
    ])
    sent_tool = next(message for message in trimmed if message.role == "tool")

    assert sent_tool.content.endswith(closing_tag)
    assert counter.count_text(sent_tool.content) <= 40


def test_structured_retrieval_context_is_not_cut_into_invalid_json():
    counter = TiktokenCounter("gpt-4o")
    manager = ContextManager(max_tokens=5000, tool_result_max_tokens=40, counter=counter)
    closing_tag = "\n</UNTRUSTED_RETRIEVED_CONTENT>"
    catalog = ",".join(
        f'{{"citation_id":"S{index}","text":"unique_fact_{index}"}}'
        for index in range(1, 9)
    )
    wrapped = (
        "<UNTRUSTED_RETRIEVED_CONTENT>\n"
        '{"retrieval_groups":[{"query":"topic","source_ids":'
        '["S1","S2","S3","S4","S5","S6","S7","S8"]}],'
        f'"source_catalog":[{catalog}]}}'
        + closing_tag
    )

    trimmed, _, _ = manager.trim_messages([
        ChatMessage(role="user", content="question"),
        ChatMessage(
            role="tool",
            content=wrapped,
            tool_call_id="call-1",
            tool_name="search_docs",
        ),
    ])
    sent_tool = next(message for message in trimmed if message.role == "tool")

    assert counter.count_text(wrapped) > 40
    assert sent_tool.content == wrapped
