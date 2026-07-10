"""Tests for BackgroundTaskManager."""
import asyncio

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

    tm.create(_ok(), "test_ok")
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

    tm.create(_fail(), "test_fail")
    await asyncio.sleep(0.1)

    status = tm.get_status()
    failed = [h for h in status["history"] if h["name"].startswith("test_fail")]
    assert len(failed) == 1
    assert failed[0]["status"] == "failed"


@pytest.mark.asyncio
async def test_get_status_shows_running():
    tm = BackgroundTaskManager()
    event = asyncio.Event()

    async def _slow():
        await event.wait()

    tm.create(_slow(), "test_slow")
    await asyncio.sleep(0.05)

    status = tm.get_status()
    assert any(n.startswith("test_slow") for n in status["running"])
    assert any(h["name"].startswith("test_slow") for h in status["history"]) is False

    event.set()
    await asyncio.sleep(0.05)

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

    tm.create(_cancel_aware(), "test_cancel")
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
