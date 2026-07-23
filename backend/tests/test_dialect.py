import pytest

from models.dialect import SqliteAdapter, UnsupportedDialectError, get_adapter


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
    from models.database import init_db, session_scope

    adapter = SqliteAdapter()
    await init_db()
    async with session_scope() as session:
        # Should not raise if WAL is active
        await adapter.health_check(session)


@pytest.mark.asyncio
async def test_sqlite_rebuild_fts_preserves_relational_bm25_schema():
    from sqlalchemy import text

    from models.database import session_scope
    from textdb.bm25_search import BM25Search

    adapter = SqliteAdapter()
    async with session_scope() as session:
        await adapter.rebuild_fts(session)

    async with session_scope() as session:
        result = await session.execute(
            text("SELECT sql FROM sqlite_master WHERE type='table' AND name='bm25_docs'")
        )
        create_sql = result.scalar_one()
        assert "VIRTUAL TABLE" not in create_sql.upper()

    bm25 = BM25Search()
    await bm25.insert("chunk-rebuild", "doc-rebuild", "部署回滚流程与检查清单")
    results = await bm25.search("回滚", top_k=3)
    assert [item.chunk_id for item in results] == ["chunk-rebuild"]
    await bm25.delete_by_document("doc-rebuild")
