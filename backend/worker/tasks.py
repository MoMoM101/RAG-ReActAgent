"""Background task manager with SQLite persistence for restart recovery."""

import asyncio
import logging
import time
import uuid
from collections.abc import Awaitable, Callable
from contextlib import suppress
from datetime import UTC, datetime
from typing import Any

logger = logging.getLogger(__name__)

_HEARTBEAT_INTERVAL = 30  # seconds between heartbeat updates
_HEARTBEAT_TIMEOUT = 120  # seconds before a "running" task is considered crashed
_HEARTBEAT_TIMEOUT_SECONDS = 120
DEAD_LETTER_STATUS = "dead_letter"


class HandlerRegistry:
    """Maps task_type strings to async handler functions."""

    def __init__(self):
        self._handlers: dict[str, Callable[[dict], Awaitable[Any]]] = {}

    def register(self, task_type: str):
        """Decorator to register a handler for a task_type."""
        def decorator(fn: Callable[[dict], Awaitable[Any]]):
            if task_type in self._handlers:
                raise ValueError(f"Handler for '{task_type}' already registered")
            self._handlers[task_type] = fn
            return fn
        return decorator

    async def dispatch(self, task_type: str, payload: dict) -> Any:
        """Invoke the handler for task_type with payload."""
        handler = self._handlers.get(task_type)
        if handler is None:
            raise KeyError(f"No handler registered for task_type='{task_type}'")
        return await handler(payload)


_handler_registry = HandlerRegistry()


def get_handler_registry() -> HandlerRegistry:
    return _handler_registry


class BackgroundTaskManager:
    """Tracks background tasks with persistence and status querying."""

    def __init__(self, max_history: int = 50):
        self._tasks: dict[str, asyncio.Task] = {}
        self._max_history = max_history
        self._history: list[dict] = []

    def create(
        self,
        work: Callable[[], Awaitable[Any]] | Awaitable[Any],
        name: str,
        *,
        metadata: dict | None = None,
        idempotency_key: str | None = None,
        task_type: str = "",
        max_attempts: int = 3,
    ) -> asyncio.Task:
        """Create and track background work.

        Callers should pass a coroutine factory. A factory is invoked only after
        the task record exists, so an immediate cancellation cannot leak an
        already-created, never-awaited coroutine.
        """
        import json as _json

        unique_name = f"{name}_{uuid.uuid4().hex[:6]}"
        meta_json = _json.dumps(metadata or {}, ensure_ascii=False)

        task = asyncio.create_task(
            self._wrap(work, unique_name, metadata, meta_json,
                       idempotency_key=idempotency_key,
                       task_type=task_type,
                       max_attempts=max_attempts)
        )
        self._tasks[unique_name] = task
        task.add_done_callback(lambda _t: self._tasks.pop(unique_name, None))
        return task

    async def _persist_create(
        self, name: str, meta_json: str,
        idempotency_key: str | None = None,
        task_type: str = "",
        max_attempts: int = 3,
    ) -> None:
        try:
            from sqlalchemy import text as sa_text

            from models.database import new_session
            session = new_session()
            try:
                conn = await session.connection()
                await conn.execute(sa_text(
                    "INSERT INTO task_queue (id, name, status, metadata, "
                    "idempotency_key, task_type, max_attempts) "
                    "VALUES (:id, :name, 'pending', :meta, :ikey, :ttype, :max_att)"
                ), {
                    "id": name,
                    "name": name.split("_")[0],
                    "meta": meta_json,
                    "ikey": idempotency_key,
                    "ttype": task_type,
                    "max_att": max_attempts,
                })
                await session.commit()
            except Exception:
                await session.rollback()
                raise
            finally:
                await session.close()
        except Exception as e:
            logger.warning("failed to persist task %s: %s", name, e)

    async def _persist_update(
        self, name: str, status: str, error: str | None = None,
        *, metadata: dict | None = None,
    ) -> None:
        try:
            from sqlalchemy import text as sa_text

            from models.database import new_session
            session = new_session()
            try:
                conn = await session.connection()
                if status == "running":
                    await conn.execute(sa_text(
                        "UPDATE task_queue SET status='running', heartbeat_at=datetime('now') "
                        "WHERE id=:id"
                    ), {"id": name})
                elif status == "retry_wait":
                    delay = (metadata or {}).get("retry_delay_sec", 60)
                    await conn.execute(sa_text(
                        "UPDATE task_queue SET status='retry_wait', error=:err, "
                        "next_run_at=datetime('now', :delay) WHERE id=:id"
                    ), {"err": error, "delay": f"+{int(delay)} seconds", "id": name})
                elif status in ("done", "failed"):
                    await conn.execute(sa_text(
                        "UPDATE task_queue SET status=:st, error=:err, "
                        "completed_at=datetime('now') WHERE id=:id"
                    ), {"st": status, "err": error, "id": name})
                await session.commit()
            except Exception:
                await session.rollback()
                raise
            finally:
                await session.close()
        except Exception:
            pass  # Best-effort persistence

    async def _persist_heartbeat(self, name: str) -> None:
        try:
            from sqlalchemy import text as sa_text

            from models.database import new_session
            session = new_session()
            try:
                conn = await session.connection()
                await conn.execute(
                    sa_text(
                        "UPDATE task_queue SET heartbeat_at=datetime('now') "
                        "WHERE id=:id AND status='running'"
                    ),
                    {"id": name},
                )
                await session.commit()
            except Exception:
                await session.rollback()
                raise
            finally:
                await session.close()
        except Exception:
            pass  # Best-effort persistence

    async def _heartbeat(self, name: str) -> None:
        while True:
            await asyncio.sleep(_HEARTBEAT_INTERVAL)
            await self._persist_heartbeat(name)

    async def _check_idempotency(self, idempotency_key: str) -> bool:
        """Return True if a task with this idempotency_key already exists (completed or pending)."""
        if not idempotency_key:
            return False
        try:
            from sqlalchemy import text as sa_text

            from models.database import new_session
            session = new_session()
            try:
                conn = await session.connection()
                row = (await conn.execute(sa_text(
                    "SELECT COUNT(*) FROM task_queue WHERE idempotency_key=:key "
                    "AND status IN ('pending', 'running', 'done')"
                ), {"key": idempotency_key})).fetchone()
                return row is not None and row[0] > 0
            except Exception:
                await session.rollback()
                raise
            finally:
                await session.close()
        except Exception:
            return False

    async def _atomic_claim_pending(self, task_id: str, worker_id: str) -> bool:
        """Atomically claim a pending or stale running task. Returns True if claimed."""
        try:
            from sqlalchemy import text as sa_text

            from models.database import new_session
            session = new_session()
            try:
                conn = await session.connection()
                result = await conn.execute(sa_text(
                    "UPDATE task_queue SET status='running', worker_id=:wid, "
                    "heartbeat_at=datetime('now'), attempt=attempt+1 "
                    "WHERE id=:id AND (status='pending' OR "
                    "(status='running' AND heartbeat_at < datetime('now', '-120 seconds')))"
                ), {"id": task_id, "wid": worker_id})
                await session.commit()
                return result.rowcount > 0
            except Exception:
                await session.rollback()
                raise
            finally:
                await session.close()
        except Exception:
            return False

    async def _move_to_dead_letter(self, task_id: str) -> bool:
        """Move a task to dead-letter state after max attempts."""
        try:
            from sqlalchemy import text as sa_text

            from models.database import new_session
            session = new_session()
            try:
                conn = await session.connection()
                result = await conn.execute(sa_text(
                    "UPDATE task_queue SET status=:dl WHERE id=:id "
                    "AND attempt >= max_attempts"
                ), {"id": task_id, "dl": DEAD_LETTER_STATUS})
                await session.commit()
                return result.rowcount > 0
            except Exception:
                await session.rollback()
                raise
            finally:
                await session.close()
        except Exception:
            return False

    async def _wrap(
        self,
        work: Callable[[], Awaitable[Any]] | Awaitable[Any],
        name: str,
        metadata: dict | None,
        meta_json: str,
        *,
        idempotency_key: str | None = None,
        task_type: str = "",
        max_attempts: int = 3,
    ):
        t0 = time.time()
        heartbeat_task: asyncio.Task | None = None

        try:
            # Check idempotency before creating
            if idempotency_key and await self._check_idempotency(idempotency_key):
                logger.info(
                    "task skipped (idempotent): key=%s name=%s",
                    idempotency_key, name,
                )
                return None

            # Keep create -> running ordered; separate fire-and-forget writes can
            # otherwise update a row before it has been inserted.
            await self._persist_create(
                name, meta_json,
                idempotency_key=idempotency_key,
                task_type=task_type,
                max_attempts=max_attempts,
            )
            await self._persist_update(name, "running")
            heartbeat_task = asyncio.create_task(self._heartbeat(name))
            coro = work() if callable(work) else work
            result = await coro
            elapsed = time.time() - t0
            logger.info(
                "background_task completed name=%s elapsed=%.2fs", name, elapsed,
            )
            self._record(name, "completed", elapsed, metadata)
            await self._persist_update(name, "done")
            return result
        except asyncio.CancelledError:
            elapsed = time.time() - t0
            logger.info("background_task cancelled name=%s elapsed=%.2fs", name, elapsed)
            self._record(name, "cancelled", elapsed, metadata)
            await self._persist_update(name, "failed", error="cancelled")
            raise
        except Exception:
            elapsed = time.time() - t0
            logger.exception(
                "background_task failed name=%s elapsed=%.2fs", name, elapsed,
            )
            self._record(name, "failed", elapsed, metadata)
            await self._persist_update(name, "failed", error="exception")
            # Move to dead-letter if max attempts reached
            await self._move_to_dead_letter(name)
        finally:
            if heartbeat_task is not None:
                heartbeat_task.cancel()
                with suppress(asyncio.CancelledError):
                    await heartbeat_task

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

    async def recover_stale_tasks(self) -> int:
        """Recover stale tasks: re-enqueue retryable, dead-letter exhausted."""
        try:
            from sqlalchemy import text as sa_text

            from models.database import new_session
            session = new_session()
            try:
                conn = await session.connection()
                # Re-enqueue retry_wait tasks whose next_run_at has passed
                retry_wait_result = await conn.execute(sa_text(
                    "UPDATE task_queue SET status='pending', worker_id=NULL, "
                    "next_run_at=NULL WHERE status='retry_wait' "
                    "AND next_run_at <= datetime('now')"
                ))
                retry_wait_count = retry_wait_result.rowcount

                # Move exhausted tasks to dead-letter
                dead_result = await conn.execute(sa_text(
                    "UPDATE task_queue SET status=:dl, error='dead_letter: max attempts reached' "
                    "WHERE status='running' AND heartbeat_at < datetime('now', :timeout) "
                    "AND attempt >= max_attempts"
                ), {"dl": DEAD_LETTER_STATUS, "timeout": f"-{_HEARTBEAT_TIMEOUT_SECONDS} seconds"})
                dead_count = dead_result.rowcount

                # Re-enqueue retryable tasks
                retry_result = await conn.execute(sa_text(
                    "UPDATE task_queue SET status='pending', worker_id=NULL "
                    "WHERE status='running' AND heartbeat_at < datetime('now', :timeout) "
                    "AND attempt < max_attempts"
                ), {"timeout": f"-{_HEARTBEAT_TIMEOUT_SECONDS} seconds"})
                retry_count = retry_result.rowcount
                await session.commit()

                total = dead_count + retry_count + retry_wait_count
                if total:
                    logger.warning(
                        "task recovery: %d dead-lettered, %d re-enqueued, %d retry_wait",
                        dead_count, retry_count, retry_wait_count,
                    )
                return total
            except Exception:
                await session.rollback()
                raise
            finally:
                await session.close()
        except Exception as e:
            logger.warning("task recovery failed: %s", e)
            return 0

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


async def recover_tasks_on_startup() -> int:
    """Recover stale tasks from the task queue. Called during app startup."""
    return await get_task_manager().recover_stale_tasks()
