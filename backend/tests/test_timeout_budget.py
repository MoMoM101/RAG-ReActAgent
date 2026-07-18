"""Phase 4: per-phase timeout budget unit tests."""

import asyncio

import pytest

from config import settings


class TestTimeoutConfig:
    def test_timeout_defaults_positive(self):
        assert settings.rag_timeout_intent > 0
        assert settings.rag_timeout_retrieval > 0
        assert settings.rag_timeout_rerank > 0
        assert settings.rag_timeout_generation > 0
        assert settings.rag_timeout_verification > 0
        assert settings.rag_timeout_repair > 0

    def test_cache_enabled_by_default(self):
        assert settings.rag_answer_cache_enabled is True

    def test_retrieval_timeout_exceeds_intent(self):
        """Retrieval should have more budget than intent classification."""
        assert settings.rag_timeout_retrieval >= settings.rag_timeout_intent

    def test_rerank_fits_within_retrieval_budget(self):
        """Rerank budget must fit inside retrieval budget (nesting invariant)."""
        assert settings.rag_timeout_retrieval >= settings.rag_timeout_rerank

    def test_generation_timeout_is_largest(self):
        """Generation should have the most budget."""
        assert settings.rag_timeout_generation >= settings.rag_timeout_retrieval
        assert settings.rag_timeout_generation >= settings.rag_timeout_verification


class TestAsyncTimeout:
    @pytest.mark.asyncio
    async def test_wait_for_returns_value_on_success(self):
        async def fast_op():
            await asyncio.sleep(0.01)
            return "result"

        result = await asyncio.wait_for(fast_op(), timeout=1.0)
        assert result == "result"

    @pytest.mark.asyncio
    async def test_wait_for_raises_timeout_error(self):
        async def slow_op():
            await asyncio.sleep(10)

        with pytest.raises(asyncio.TimeoutError):
            await asyncio.wait_for(slow_op(), timeout=0.05)

    @pytest.mark.asyncio
    async def test_wait_for_timeout_pattern(self):
        """The pattern used in agent loop: catch TimeoutError, return fallback."""
        async def maybe_slow():
            await asyncio.sleep(0.02)
            return "ok"

        try:
            result = await asyncio.wait_for(maybe_slow(), timeout=0.005)
        except asyncio.TimeoutError:
            result = "timeout_fallback"

        assert result == "timeout_fallback"
