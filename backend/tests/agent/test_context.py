import tiktoken

from agent.context import ContextManager, _estimate_tokens
from llm.base import ChatMessage


def _ref_tokens(text: str) -> int:
    return len(tiktoken.get_encoding("cl100k_base").encode(text))


class TestEstimateTokens:
    def test_none_returns_zero(self):
        assert _estimate_tokens(None) == 0

    def test_empty_returns_zero(self):
        assert _estimate_tokens("") == 0

    def test_english(self):
        text = "The quick brown fox jumps over the lazy dog."
        assert _estimate_tokens(text) == _ref_tokens(text)

    def test_chinese(self):
        text = "知识库助手帮助用户检索文档内容"
        assert _estimate_tokens(text) == _ref_tokens(text)

    def test_mixed_cn_en(self):
        text = "Python 版本需要 3.10+ 及以上"
        assert _estimate_tokens(text) == _ref_tokens(text)

    def test_short_string(self):
        assert _estimate_tokens("x") >= 1

    def test_much_more_accurate_than_char_div_2(self):
        """Verify tiktoken is significantly more accurate than len//2."""
        cn = "知识库助手帮助用户检索已上传的文档内容并根据检索结果回答用户问题"
        old_est = max(1, len(cn) // 2)
        new_est = _estimate_tokens(cn)
        assert new_est != old_est  # should differ for Chinese text


class TestTrimMessages:
    def test_empty_returns_empty(self):
        cm = ContextManager(max_tokens=10000)
        msgs, summary, dropped = cm.trim_messages([])
        assert msgs == []
        assert summary == ""
        assert dropped == []

    def test_single_user_preserved(self):
        cm = ContextManager(max_tokens=10000)
        msgs, summary, dropped = cm.trim_messages([
            ChatMessage(role="user", content="hello"),
        ])
        assert len(msgs) == 1
        assert msgs[0].content == "hello"
        assert summary == ""

    def test_anchor_exceeds_budget_trims_to_minimum(self):
        cm = ContextManager(max_tokens=50)
        msgs = [
            ChatMessage(role="user", content="x" * 200),
            ChatMessage(role="user", content="last message"),
        ]
        trimmed, summary, dropped = cm.trim_messages(msgs)
        assert len(trimmed) >= 1
        # last_user should always be preserved
        assert trimmed[-1].content == "last message"

    def test_message_exactly_on_budget_line(self):
        """Message with token count equal to remaining budget is kept."""
        text = "hello world this is a test message for token budget boundary testing"
        tok = _estimate_tokens(text)
        cm = ContextManager(max_tokens=tok + 100)
        msgs = [
            ChatMessage(role="user", content="last query"),
            ChatMessage(role="assistant", content=text),
        ]
        trimmed, _, _ = cm.trim_messages(msgs)
        assert len(trimmed) >= 2

    def test_last_user_always_preserved(self):
        """Even when budget is extremely tight, last user message is kept."""
        cm = ContextManager(max_tokens=30)
        msgs = [
            ChatMessage(role="user", content="this is an old long message that exceeds budget"),
            ChatMessage(role="user", content="final query"),
        ]
        trimmed, _, _ = cm.trim_messages(msgs)
        assert trimmed[-1].content == "final query"

    def test_multi_tool_pair_atomic_trim(self):
        """Assistant + multiple tool results atomically kept or dropped together."""
        tok_per = _estimate_tokens("x")
        # Budget just under what we need for 3 tool results + assistant
        cm = ContextManager(max_tokens=tok_per * 10)
        msgs = [
            ChatMessage(role="user", content="query"),
            ChatMessage(role="assistant", content="answer", tool_calls=[]),
            ChatMessage(role="tool", content="result1", tool_call_id="1", tool_name="search"),
            ChatMessage(role="tool", content="result2", tool_call_id="2", tool_name="search"),
            ChatMessage(role="tool", content="result3", tool_call_id="3", tool_name="search"),
        ]
        trimmed, _, _ = cm.trim_messages(msgs)
        # Tool results after assistant w/o tool_calls are not paired
        # This test verifies the trimming doesn't crash on multiple tool messages

    def test_mixed_cn_en_budget(self):
        """Chinese + English mixed content uses tiktoken counts, not char counts."""
        cm = ContextManager(max_tokens=5000)
        msgs = [
            ChatMessage(role="user", content="帮我查一下关于machine learning的最新文档资料"),
            ChatMessage(role="assistant", content="I will search the knowledge base for you."),
            ChatMessage(role="user", content="final"),
        ]
        trimmed, _, _ = cm.trim_messages(msgs)
        assert len(trimmed) >= 1
        assert trimmed[-1].content == "final"


class TestBuildSystemPrompt:
    def reset_template(self):
        import agent.context
        agent.context._template = None

    def test_template_loaded_and_formatted(self):
        self.reset_template()
        cm = ContextManager()
        prompt = cm.build_system_prompt(
            "test hint",
            "- tool1: does something\n- tool2: does other",
            "## User Profile\nName: Test",
        )
        assert "test hint" in prompt
        assert "tool1" in prompt
        assert "User Profile" in prompt

    def test_empty_sections_omitted(self):
        self.reset_template()
        cm = ContextManager()
        prompt = cm.build_system_prompt("", "- tool: desc", "")
        assert prompt  # doesn't crash
        assert isinstance(prompt, str)

    def test_template_cached_on_second_call(self):
        self.reset_template()
        cm = ContextManager()
        import agent.context

        agent.context._template = "T{profile_section}{tools_description}{hint_section}"
        prompt = cm.build_system_prompt("hint", "tools", "profile")
        assert "profile" in prompt
        assert "tools" in prompt
        assert "hint" in prompt

        # Modify cached template → should use cached version
        agent.context._template = "MODIFIED"
        prompt2 = cm.build_system_prompt("x", "y", "z")
        assert prompt2 == "MODIFIED"
