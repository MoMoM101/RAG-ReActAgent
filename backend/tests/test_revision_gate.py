"""Database revision gate behavior tests."""

import sqlite3
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest


async def test_auto_migrate_upgrades_an_existing_old_revision():
    with (
        patch(
            "models.database._current_revision",
            new_callable=AsyncMock,
            side_effect=["0002", "0003"],
        ),
        patch("models.database._head_revision", return_value="0003"),
        patch("models.database._auto_migrate_enabled", return_value=True),
        patch(
            "models.database._backup_before_migration",
            return_value=Path("migration.sqlite3"),
        ),
        patch("alembic.command.upgrade") as upgrade,
    ):
        from models.database import check_revision_gate

        await check_revision_gate()

    upgrade.assert_called_once()
    assert upgrade.call_args.args[1] == "head"


async def test_auto_migrate_refuses_to_blindly_stamp_legacy_database(setup_db):
    with (
        patch(
            "models.database._current_revision",
            new_callable=AsyncMock,
            return_value=None,
        ),
        patch("models.database._head_revision", return_value="0003"),
        patch("models.database._auto_migrate_enabled", return_value=True),
        patch("alembic.command.stamp") as stamp,
    ):
        from models.database import check_revision_gate

        with pytest.raises(RuntimeError, match="Refusing to stamp head"):
            await check_revision_gate()

    stamp.assert_not_called()


def test_migration_snapshot_can_restore_database(tmp_path):
    from models.database import _backup_before_migration, _restore_migration_backup

    db_path = tmp_path / "live.db"
    with sqlite3.connect(db_path) as connection:
        connection.execute("CREATE TABLE sample (value TEXT NOT NULL)")
        connection.execute("INSERT INTO sample VALUES ('before')")
        connection.commit()

    snapshot = _backup_before_migration(str(db_path), "0002", "0003")
    with sqlite3.connect(db_path) as connection:
        connection.execute("UPDATE sample SET value='after'")
        connection.commit()

    _restore_migration_backup(snapshot, str(db_path))

    with sqlite3.connect(db_path) as connection:
        value = connection.execute("SELECT value FROM sample").fetchone()[0]
    assert value == "before"
    assert snapshot.parent.name == "migration_backups"


async def test_auto_migration_failure_restores_snapshot(setup_db):
    snapshot = Path("pre-migration.sqlite3")
    with (
        patch(
            "models.database._current_revision",
            new_callable=AsyncMock,
            return_value="0002",
        ),
        patch("models.database._head_revision", return_value="0003"),
        patch("models.database._auto_migrate_enabled", return_value=True),
        patch(
            "models.database._backup_before_migration",
            return_value=snapshot,
        ),
        patch("models.database._restore_migration_backup") as restore,
        patch("alembic.command.upgrade", side_effect=RuntimeError("migration failed")),
    ):
        from models.database import check_revision_gate

        with pytest.raises(RuntimeError, match="restored snapshot"):
            await check_revision_gate()

    restore.assert_called_once()
    assert restore.call_args.args[0] == snapshot
