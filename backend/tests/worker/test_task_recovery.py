"""Test background task idempotent replay and recovery."""

import asyncio
import uuid

import pytest
from sqlalchemy import text as sa_text

from models.database import session_scope


@pytest.fixture(autouse=True)
async def _cleanup():
    yield
    from worker.tasks import reset_task_manager
    reset_task_manager()
    async with session_scope() as session:
        conn = await session.connection()
        await conn.execute(sa_text("DELETE FROM task_queue"))
        await session.commit()


class TestHandlerRegistry:
    async def test_register_and_dispatch(self):
        from worker.tasks import HandlerRegistry

        registry = HandlerRegistry()
        results = []

        @registry.register("test_echo")
        async def echo_handler(payload: dict) -> dict:
            results.append(payload)
            return {"echo": payload}

        await registry.dispatch("test_echo", {"msg": "hello"})
        assert len(results) == 1
        assert results[0] == {"msg": "hello"}

    async def test_duplicate_register_raises(self):
        from worker.tasks import HandlerRegistry

        registry = HandlerRegistry()

        @registry.register("test_dup")
        async def first(payload): ...

        with pytest.raises(ValueError, match="already registered"):
            @registry.register("test_dup")
            async def second(payload): ...

    async def test_unregistered_type_raises_key_error(self):
        from worker.tasks import HandlerRegistry

        registry = HandlerRegistry()
        with pytest.raises(KeyError):
            await registry.dispatch("nonexistent", {})


class TestDeadLetter:
    async def test_max_attempts_moves_to_dead_letter(self):
        from worker.tasks import DEAD_LETTER_STATUS, BackgroundTaskManager

        tm = BackgroundTaskManager()
        task_id = f"dl_test_{uuid.uuid4().hex[:8]}"

        async with session_scope() as session:
            conn = await session.connection()
            await conn.execute(sa_text(
                "INSERT INTO task_queue (id, name, status, task_type, attempt, max_attempts, payload_json) "
                "VALUES (:id, 'dl_test', 'failed', 'test', 3, 3, '{}')"
            ), {"id": task_id})
            await session.commit()

        moved = await tm._move_to_dead_letter(task_id)
        assert moved is True

        async with session_scope() as session:
            conn = await session.connection()
            row = (await conn.execute(
                sa_text("SELECT status FROM task_queue WHERE id=:id"), {"id": task_id}
            )).fetchone()
        assert row is not None
        assert row[0] == DEAD_LETTER_STATUS


class TestRecoveryOnStartup:
    async def test_recover_replays_retryable_tasks(self):
        from worker.tasks import (
            get_handler_registry,
            get_task_manager,
            recover_tasks_on_startup,
        )

        task_id = f"recover_test_{uuid.uuid4().hex[:8]}"
        task_type = f"test_recover_{uuid.uuid4().hex[:8]}"
        completed = asyncio.Event()

        @get_handler_registry().register(task_type)
        async def recovered_handler(payload: dict) -> None:
            assert payload == {"value": 42}
            completed.set()

        async with session_scope() as session:
            conn = await session.connection()
            await conn.execute(sa_text(
                "INSERT INTO task_queue (id, name, status, task_type, payload_json, "
                "attempt, max_attempts, heartbeat_at) "
                "VALUES (:id, 'recover_test', 'running', :task_type, :payload, "
                "0, 3, datetime('now', '-200 seconds'))"
            ), {
                "id": task_id,
                "task_type": task_type,
                "payload": '{"value": 42}',
            })
            await session.commit()

        recovered = await recover_tasks_on_startup()
        assert recovered >= 1
        await asyncio.wait_for(completed.wait(), timeout=1)
        recovered_task = get_task_manager()._tasks.get(task_id)
        if recovered_task is not None:
            await asyncio.wait_for(recovered_task, timeout=1)

        async with session_scope() as session:
            conn = await session.connection()
            row = (await conn.execute(
                sa_text("SELECT status, attempt FROM task_queue WHERE id=:id"), {"id": task_id}
            )).fetchone()
        assert row[0] == "done"
        assert row[1] == 1
