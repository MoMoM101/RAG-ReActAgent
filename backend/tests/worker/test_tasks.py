"""Tests for BackgroundTaskManager."""

import asyncio
from unittest.mock import AsyncMock

import pytest

from worker.tasks import BackgroundTaskManager, get_task_manager, reset_task_manager


@pytest.fixture(autouse=True)
def _reset():
    reset_task_manager()
    yield
    reset_task_manager()


@pytest.mark.asyncio
async def test_create_and_complete():
    tm = BackgroundTaskManager()

    async def _ok():
        return 42

    tm.create(_ok, "test_ok")
    # Task runs immediately with asyncio.create_task, wait briefly
    await asyncio.sleep(0.1)

    status = tm.get_status()
    assert len(status["running"]) == 0  # completed tasks are removed
    completed = [h for h in status["history"] if h["name"].startswith("test_ok")]
    assert len(completed) == 1
    assert completed[0]["status"] == "completed"


@pytest.mark.asyncio
async def test_create_and_fail():
    tm = BackgroundTaskManager()

    async def _fail():
        raise RuntimeError("boom")

    tm.create(_fail, "test_fail")
    await asyncio.sleep(0.1)

    status = tm.get_status()
    failed = [h for h in status["history"] if h["name"].startswith("test_fail")]
    assert len(failed) == 1
    assert failed[0]["status"] == "failed"


@pytest.mark.asyncio
async def test_get_status_shows_running():
    tm = BackgroundTaskManager()
    event = asyncio.Event()
    started = asyncio.Event()

    async def _slow():
        started.set()
        await event.wait()

    task = tm.create(_slow, "test_slow")
    await asyncio.wait_for(started.wait(), timeout=1)

    status = tm.get_status()
    assert any(n.startswith("test_slow") for n in status["running"])
    assert any(h["name"].startswith("test_slow") for h in status["history"]) is False

    event.set()
    await asyncio.wait_for(task, timeout=1)

    status2 = tm.get_status()
    assert not any(n.startswith("test_slow") for n in status2["running"])
    assert any(h["name"].startswith("test_slow") for h in status2["history"])


@pytest.mark.asyncio
async def test_shutdown_cancels_running_tasks():
    tm = BackgroundTaskManager()
    started = asyncio.Event()
    cancelled = asyncio.Event()

    async def _cancel_aware():
        started.set()
        try:
            await asyncio.Event().wait()  # sleep forever
        except asyncio.CancelledError:
            cancelled.set()

    tm.create(_cancel_aware, "test_cancel")
    await started.wait()

    await tm.shutdown()
    await asyncio.sleep(0.05)

    status = tm.get_status()
    assert not any(n.startswith("test_cancel") for n in status["running"])
    assert cancelled.is_set()


@pytest.mark.asyncio
async def test_get_task_manager_singleton():
    tm1 = get_task_manager()
    tm2 = get_task_manager()
    assert tm1 is tm2


@pytest.mark.asyncio
async def test_create_orders_persistence_before_running(monkeypatch):
    tm = BackgroundTaskManager()
    calls: list[str] = []

    async def _create(*_args, **_kwargs):
        calls.append("create")

    async def _update(_name, status, error=None):
        calls.append(status)

    monkeypatch.setattr(tm, "_persist_create", _create)
    monkeypatch.setattr(tm, "_persist_update", _update)

    task = tm.create(lambda: asyncio.sleep(0), "ordered")
    await task

    assert calls[:2] == ["create", "running"]
    assert calls[-1] == "done"


@pytest.mark.asyncio
async def test_immediate_cancel_does_not_create_coroutine(monkeypatch):
    tm = BackgroundTaskManager()
    invoked = False

    async def _persist_create(*_args, **_kwargs):
        await asyncio.sleep(0)

    def _factory():
        nonlocal invoked
        invoked = True
        return asyncio.sleep(0)

    monkeypatch.setattr(tm, "_persist_create", _persist_create)
    task = tm.create(_factory, "cancel_before_start")
    task.cancel()

    with pytest.raises(asyncio.CancelledError):
        await task
    assert invoked is False


@pytest.mark.asyncio
async def test_running_task_sends_heartbeat(monkeypatch):
    tm = BackgroundTaskManager()
    heartbeat = AsyncMock()
    finished = asyncio.Event()

    monkeypatch.setattr("worker.tasks._HEARTBEAT_INTERVAL", 0.01)
    monkeypatch.setattr(tm, "_persist_create", AsyncMock())
    monkeypatch.setattr(tm, "_persist_update", AsyncMock())
    monkeypatch.setattr(tm, "_persist_heartbeat", heartbeat)

    async def _work():
        await finished.wait()

    task = tm.create(_work, "heartbeat")
    await asyncio.sleep(0.04)
    finished.set()
    await task

    assert heartbeat.await_count >= 1


@pytest.mark.asyncio
async def test_terminal_task_rows_are_bounded():
    from sqlalchemy import text

    from models.database import session_scope

    tm = BackgroundTaskManager(max_persisted_history=2)
    for index in range(4):
        task = tm.create(lambda: asyncio.sleep(0), f"bounded_{index}")
        await task

    async with session_scope() as session:
        terminal_count = await session.scalar(text(
            "SELECT COUNT(*) FROM task_queue "
            "WHERE status IN ('done', 'failed', 'dead_letter')"
        ))

    assert terminal_count == 2


@pytest.mark.asyncio
async def test_durable_task_persists_payload_and_attempt():
    import json

    from sqlalchemy import text

    from models.database import session_scope

    tm = BackgroundTaskManager()
    task = tm.create(
        lambda: asyncio.sleep(0),
        "durable_payload",
        task_type="test_payload",
        payload={"conversation_id": "conv-1", "count": 2},
    )
    await task

    async with session_scope() as session:
        row = (
            await session.execute(text(
                "SELECT status, task_type, payload_json, attempt FROM task_queue "
                "WHERE task_type='test_payload' ORDER BY created_at DESC LIMIT 1"
            ))
        ).fetchone()

    assert row is not None
    assert row[0] == "done"
    assert row[1] == "test_payload"
    assert json.loads(row[2]) == {"conversation_id": "conv-1", "count": 2}
    assert row[3] == 1


@pytest.mark.asyncio
async def test_recovery_monitor_is_singleton_and_stops_on_shutdown():
    tm = BackgroundTaskManager()

    tm.start_recovery_monitor()
    first = tm._tasks["task_recovery_monitor"]
    tm.start_recovery_monitor()

    assert tm._tasks["task_recovery_monitor"] is first
    await tm.shutdown()
    assert first.cancelled()


@pytest.mark.asyncio
async def test_paused_manager_closes_precreated_coroutine():
    tm = BackgroundTaskManager()
    tm._paused = True
    work = asyncio.sleep(0)

    rejected = tm.create(work, "during_maintenance")
    await rejected

    assert work.cr_frame is None
