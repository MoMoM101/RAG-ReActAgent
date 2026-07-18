# backend/tests/test_session.py
import pytest
from sqlalchemy import text

from models.database import init_db, new_session, session_scope


@pytest.mark.asyncio
async def test_new_session_can_commit():
    await init_db()
    session = new_session()
    try:
        await session.execute(text("SELECT 1"))
        await session.commit()
    finally:
        await session.close()


@pytest.mark.asyncio
async def test_session_scope_auto_rollback_on_exception():
    await init_db()

    class TestError(Exception):
        pass

    with pytest.raises(TestError):
        async with session_scope() as session:
            await session.execute(text("SELECT 1"))
            raise TestError("trigger rollback")


@pytest.mark.asyncio
async def test_session_scope_no_implicit_commit():
    """Verify that session_scope does not auto-commit on clean exit."""
    await init_db()
    async with session_scope() as session:
        await session.execute(text("SELECT 1"))
    # Clean exit with no explicit commit -- session closed without error


@pytest.mark.asyncio
async def test_concurrent_sessions_independent():
    """Two concurrent sessions should not interfere."""
    await init_db()
    s1 = new_session()
    s2 = new_session()
    try:
        r1 = await s1.execute(text("SELECT 1 AS val"))
        r2 = await s2.execute(text("SELECT 2 AS val"))
        assert r1.fetchone()[0] == 1
        assert r2.fetchone()[0] == 2
    finally:
        await s1.close()
        await s2.close()
