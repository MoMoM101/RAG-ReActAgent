"""Background task manager — unified lifecycle and observability."""

import asyncio
import logging
import time
from contextlib import suppress
from datetime import UTC, datetime

logger = logging.getLogger(__name__)


class BackgroundTaskManager:
    """Tracks background tasks with structured logging and status querying."""

    def __init__(self, max_history: int = 50):
        self._tasks: dict[str, asyncio.Task] = {}
        self._max_history = max_history
        self._history: list[dict] = []

    def create(
        self, coro, name: str, *, metadata: dict | None = None
    ) -> asyncio.Task:
        """Wrap and track a background coroutine."""
        # Append short random suffix to avoid name collisions
        import uuid as _uuid
        unique_name = f"{name}_{_uuid.uuid4().hex[:6]}"
        task = asyncio.create_task(self._wrap(coro, unique_name, metadata))
        self._tasks[unique_name] = task
        task.add_done_callback(lambda _t: self._tasks.pop(unique_name, None))
        return task

    async def _wrap(self, coro, name: str, metadata: dict | None):
        t0 = time.time()
        try:
            result = await coro
            elapsed = time.time() - t0
            logger.info(
                "background_task completed name=%s elapsed=%.2fs", name, elapsed
            )
            self._record(name, "completed", elapsed, metadata)
            return result
        except asyncio.CancelledError:
            elapsed = time.time() - t0
            logger.info("background_task cancelled name=%s elapsed=%.2fs", name, elapsed)
            self._record(name, "cancelled", elapsed, metadata)
        except Exception:
            elapsed = time.time() - t0
            logger.exception(
                "background_task failed name=%s elapsed=%.2fs", name, elapsed
            )
            self._record(name, "failed", elapsed, metadata)

    def _record(
        self, name: str, status: str, elapsed: float,
        metadata: dict | None = None,
    ):
        self._history.append({
            "name": name,
            "status": status,
            "elapsed_ms": int(elapsed * 1000),
            "metadata": metadata or {},
            "ts": datetime.now(UTC).isoformat(),
        })
        if len(self._history) > self._max_history:
            self._history.pop(0)

    async def shutdown(self):
        """Cancel all running tasks on shutdown, waiting for completion."""
        remaining = list(self._tasks.items())
        for name, task in remaining:
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task
            logger.info("background_task shutdown name=%s", name)

    def get_status(self) -> dict:
        """Return current running tasks and recent history."""
        running = list(self._tasks.keys())
        return {
            "running": running,
            "history": list(self._history[-20:]),
        }


_tm_instance: BackgroundTaskManager | None = None


def get_task_manager() -> BackgroundTaskManager:
    global _tm_instance
    if _tm_instance is None:
        _tm_instance = BackgroundTaskManager()
    return _tm_instance


def reset_task_manager() -> None:
    """Test-only: reset the singleton."""
    global _tm_instance
    _tm_instance = None
