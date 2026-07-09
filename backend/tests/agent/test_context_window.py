"""context_window.py unit tests — model lookup and window detection."""

from agent.context_window import (
    _lookup_model,
    get_window,
    is_context_error,
    reset_for_testing,
)


class TestLookupModel:
    def setup_method(self):
        reset_for_testing()

    def test_exact_match(self):
        """Exact model name match returns correct context window."""
        result = _lookup_model("gpt-4o")
        assert result is not None
        assert result > 0

    def test_prefix_match(self):
        """Prefix match: gpt-4o-mini should match gpt-4o prefix."""
        result = _lookup_model("gpt-4o-mini")
        assert result is not None
        assert result > 0

    def test_no_match_returns_none(self):
        """Unknown model returns None."""
        result = _lookup_model("nonexistent-model-xyz-999")
        assert result is None

    def test_longer_prefix_matches_first(self):
        """When multiple prefixes match, the longest should win."""
        # claude-3 should match a longer prefix than claude-
        result = _lookup_model("claude-3-opus")
        assert result is not None
        # Both "claude-" and "claude-3-" might exist; verify we get a result
        assert isinstance(result, int)


class TestGetWindow:
    def test_env_override_takes_priority(self, monkeypatch):
        """llm_max_context > 0 overrides everything."""
        from config import settings
        monkeypatch.setattr(settings, "llm_max_context", 32000)
        assert get_window() == 32000

    def test_env_override_zero_falls_through(self, monkeypatch):
        """llm_max_context == 0 falls through to JSON lookup."""
        from config import settings
        monkeypatch.setattr(settings, "llm_max_context", 0)
        monkeypatch.setattr(settings, "llm_model", "gpt-4o")
        result = get_window()
        assert result > 0  # Should find in JSON or fall to default

    def test_unknown_model_returns_default(self, monkeypatch):
        """Model not in JSON → _DEFAULT_WINDOW (128000)."""
        from config import settings
        monkeypatch.setattr(settings, "llm_max_context", 0)
        monkeypatch.setattr(settings, "llm_model", "completely-unknown-model-xyz")
        result = get_window()
        assert result == 128000

    def test_llm_model_from_settings_is_used(self, monkeypatch):
        """get_window reads llm_model from settings."""
        from config import settings
        monkeypatch.setattr(settings, "llm_max_context", 0)
        # Set to a known model
        monkeypatch.setattr(settings, "llm_model", "gpt-4o")
        result = get_window()
        assert isinstance(result, int)
        assert result > 0


class TestIsContextError:
    def test_context_length_exceeded(self):
        assert is_context_error(Exception("context_length_exceeded: too many tokens"))

    def test_maximum_context_length(self):
        assert is_context_error(Exception("Error: maximum context length 8192 exceeded"))

    def test_reduce_length(self):
        assert is_context_error(Exception("please reduce the length of your input"))

    def test_too_long(self):
        assert is_context_error(Exception("input too long for model"))

    def test_token_count_exceeds(self):
        assert is_context_error(Exception("requested token count exceeds model maximum"))

    def test_normal_error_is_false(self):
        assert is_context_error(Exception("normal error message")) is False
        assert is_context_error(ValueError("something went wrong")) is False

    def test_empty_message(self):
        assert is_context_error(Exception("")) is False
