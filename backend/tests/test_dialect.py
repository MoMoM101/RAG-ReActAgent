# backend/tests/test_dialect.py
import pytest
from models.dialect import get_adapter, UnsupportedDialectError, SqliteAdapter


def test_get_adapter_sqlite_returns_sqlite_adapter():
    adapter = get_adapter("sqlite")
    assert isinstance(adapter, SqliteAdapter)
    assert adapter.dialect_name == "sqlite"
    assert adapter.supports_fts is True


def test_get_adapter_unknown_raises():
    with pytest.raises(UnsupportedDialectError, match="postgresql"):
        get_adapter("postgresql")

    with pytest.raises(UnsupportedDialectError, match="mysql"):
        get_adapter("mysql")


@pytest.mark.asyncio
async def test_sqlite_health_check_wal():
    from models.database import async_session, init_db
    adapter = SqliteAdapter()
    await init_db()
    async with async_session() as session:
        # Should not raise if WAL is active
        await adapter.health_check(session)


@pytest.mark.asyncio
async def test_sqlite_rebuild_fts_creates_table():
    from models.database import async_session
    from sqlalchemy import text
    adapter = SqliteAdapter()
    async with async_session() as session:
        await adapter.rebuild_fts(session)
    # Verify table exists after rebuild (FTS5 virtual tables appear in sqlite_master)
    async with async_session() as session:
        result = await session.execute(
            text("SELECT name FROM sqlite_master WHERE type='table' AND name='bm25_docs'")
        )
        assert result.fetchone() is not None
