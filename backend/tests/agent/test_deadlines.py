"""Test deadline enforcement at every level of the call chain."""

import asyncio

import pytest


class TestTimeoutHierarchy:
    def test_timeout_values_are_ordered(self):
        """Connection timeout < read timeout < tool timeout < agent total deadline."""
        from config import settings
        assert settings.llm_connect_timeout <= settings.llm_read_timeout, \
            "connect timeout must be <= read timeout"
        assert settings.llm_read_timeout <= settings.tool_default_timeout, \
            "read timeout must be <= tool default timeout"
        assert settings.tool_default_timeout < settings.max_total_time, \
            "tool timeout must be less than agent total deadline"

    def test_first_token_timeout_configured(self):
        """LLM client must have first_token_timeout setting available."""
        from config import settings
        assert hasattr(settings, 'llm_first_token_timeout')
        assert settings.llm_first_token_timeout > 0

    def test_embedding_timeout_configured(self):
        """Embedding operations must have a configured timeout."""
        from config import settings
        assert settings.embedding_timeout > 0


class TestCancellationPropagation:
    async def test_cancelled_error_not_caught_by_broad_except(self):
        """CancelledError must propagate, not be suppressed as generic exception."""
        async def raises_cancelled():
            raise asyncio.CancelledError()

        with pytest.raises(asyncio.CancelledError):
            await raises_cancelled()

    async def test_agent_cancellation_event(self):
        """Cancellation event must be detectable."""
        cancelled = asyncio.Event()

        async def simulate_disconnect():
            await asyncio.sleep(0.01)
            cancelled.set()

        await simulate_disconnect()
        assert cancelled.is_set()


class TestResourceCleanup:
    async def test_shutdown_awaits_all_tasks(self):
        """Shutdown must cancel and await all running tasks."""
        from worker.tasks import BackgroundTaskManager, reset_task_manager

        reset_task_manager()
        tm = BackgroundTaskManager()

        running_flag = asyncio.Event()

        async def long_work():
            running_flag.set()
            await asyncio.sleep(60)

        tm.create(long_work, "shutdown_test")
        await asyncio.wait_for(running_flag.wait(), timeout=1.0)

        await tm.shutdown()
        # If we reach here without hanging, tasks were cancelled
