import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from models.fingerprint import compute_fingerprint, diff_fingerprint, fingerprint_matches


@pytest.mark.asyncio
async def test_fingerprint_is_deterministic_and_detects_schema_change():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    try:
        async with engine.begin() as connection:
            await connection.execute(
                text("CREATE TABLE items (id INTEGER PRIMARY KEY, name TEXT NOT NULL)")
            )
            first = await compute_fingerprint(connection)
            second = await compute_fingerprint(connection)

            assert first == second
            assert len(first) == 64
            assert await fingerprint_matches(connection, first) is True
            assert await diff_fingerprint(connection, first) == []

            await connection.execute(text("ALTER TABLE items ADD COLUMN note TEXT"))

            assert await fingerprint_matches(connection, first) is False
            differences = await diff_fingerprint(connection, first)
            assert differences[0].startswith("Fingerprint mismatch:")
            assert "Tables present: items" in differences
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_fingerprint_changes_when_index_is_added():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    try:
        async with engine.begin() as connection:
            await connection.execute(text("CREATE TABLE items (id INTEGER, name TEXT)"))
            before = await compute_fingerprint(connection)
            await connection.execute(text("CREATE INDEX ix_items_name ON items (name)"))
            after = await compute_fingerprint(connection)

            assert after != before
    finally:
        await engine.dispose()
