from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path

from sqlalchemy import event
from sqlalchemy import text as sa_text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase

from config import settings

engine = create_async_engine(settings.database_url, echo=False)
async_session = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


def new_session(**overrides) -> AsyncSession:
    """Create a new independent session.

    Caller is responsible for commit/rollback/close.
    Prefer session_scope() for automatic cleanup.
    """
    return async_session(**overrides)


@asynccontextmanager
async def session_scope() -> AsyncIterator[AsyncSession]:
    """Async context manager that ensures session cleanup.

    Closes the session on exit.  Rolls back on exception.
    Does NOT auto-commit -- caller must call commit() explicitly.
    """
    session = new_session()
    try:
        yield session
    except Exception:
        await session.rollback()
        raise
    finally:
        await session.close()


@event.listens_for(engine.sync_engine, "connect")
def _set_sqlite_pragmas(dbapi_connection, connection_record):
    """Set per-connection PRAGMAs for every connection from the pool."""
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA foreign_keys = ON")
    cursor.execute("PRAGMA busy_timeout = 30000")
    cursor.close()


class Base(DeclarativeBase):
    pass


async def init_db():
    # Schema DDL is managed by Alembic. This function handles runtime initialization only.
    import models.orm  # noqa: F401  # ensure ORM models are registered with Base.metadata

    async with engine.begin() as conn:
        await conn.exec_driver_sql("PRAGMA journal_mode=WAL")

        # Dev convenience fallback: create missing tables when AUTO_MIGRATE is on
        if _auto_migrate_enabled():
            await conn.run_sync(Base.metadata.create_all)


async def _current_revision(conn) -> str | None:
    """Return the current Alembic revision or None if no version table."""
    try:
        result = await conn.execute(sa_text("SELECT version_num FROM alembic_version"))
        row = result.fetchone()
        return row[0] if row else None
    except Exception:
        return None


def _head_revision() -> str:
    """Return the head revision from Alembic config."""
    import os as _os
    from pathlib import Path as _Path

    from alembic.config import Config as AlembicConfig
    from alembic.script import ScriptDirectory

    backend_dir = _Path(__file__).resolve().parent.parent
    alembic_ini = str(backend_dir / "alembic.ini")
    _os.chdir(str(backend_dir))
    cfg = AlembicConfig(alembic_ini)
    heads = ScriptDirectory.from_config(cfg).get_heads()
    if not heads:
        raise RuntimeError("No Alembic head revision found")
    return heads[0]


def _auto_migrate_enabled() -> bool:
    import os as _os
    env_val = _os.getenv("AUTO_MIGRATE", "")
    if not env_val:
        try:
            from config import settings
            env_val = str(getattr(settings, "auto_migrate", "") or "")
        except Exception:
            pass
    return env_val.lower() in ("1", "true", "yes")


def _backup_before_migration(
    db_path: str,
    current_revision: str,
    target_revision: str,
) -> Path:
    """Create and verify a consistent SQLite snapshot before schema changes."""
    import re
    import sqlite3

    source_path = Path(db_path).resolve()
    backup_dir = source_path.parent / "migration_backups"
    backup_dir.mkdir(parents=True, exist_ok=True)
    safe_current = re.sub(r"[^A-Za-z0-9_.-]", "_", current_revision)
    safe_target = re.sub(r"[^A-Za-z0-9_.-]", "_", target_revision)
    timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
    backup_path = backup_dir / (
        f"{source_path.stem}_{safe_current}_to_{safe_target}_{timestamp}.sqlite3"
    )
    with sqlite3.connect(source_path) as source, sqlite3.connect(backup_path) as target:
        source.backup(target)
        result = target.execute("PRAGMA quick_check").fetchone()
        if not result or result[0] != "ok":
            raise RuntimeError(f"pre-migration backup integrity check failed: {result}")

    backups = sorted(
        backup_dir.glob(f"{source_path.stem}_*_to_*.sqlite3"),
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )
    for expired in backups[settings.migration_backup_keep:]:
        expired.unlink(missing_ok=True)
    return backup_path


def _restore_migration_backup(backup_path: Path, db_path: str) -> None:
    """Restore a verified snapshot into the live SQLite database."""
    import sqlite3

    with sqlite3.connect(backup_path) as source, sqlite3.connect(db_path) as target:
        source.backup(target)
        result = target.execute("PRAGMA quick_check").fetchone()
        if not result or result[0] != "ok":
            raise RuntimeError(f"restored database integrity check failed: {result}")


async def check_revision_gate() -> None:
    """Startup gate: verify the database schema revision is current.

    Raises RuntimeError with a descriptive message when the database
    is not at the expected revision.
    """
    import os as _os
    from pathlib import Path as _Path

    gate_engine = create_async_engine(settings.database_url, echo=False)
    try:
        db_path = settings.database_url.replace("sqlite+aiosqlite:///", "")
        # db_path may be relative; resolve it
        if not _os.path.isabs(db_path):
            db_path = str((_Path(__file__).resolve().parent.parent / db_path).resolve())
        db_exists = _os.path.exists(db_path) and _os.path.getsize(db_path) > 0

        async with gate_engine.connect() as conn:
            current = await _current_revision(conn)

        head = _head_revision()

        if current is None:
            if db_exists:
                raise RuntimeError(
                    "Database exists but has no Alembic revision. "
                    "Refusing to stamp head because stamping does not apply missing schema changes. "
                    "Run the legacy database adoption procedure: "
                    "1) backup, 2) compute fingerprint, 3) stamp if matching."
                )
            elif _auto_migrate_enabled():
                # Dev/test: auto-migrate empty DB

                from alembic.config import Config as AlcCfg

                from alembic import command as alc_cmd

                backend_dir = str(_Path(__file__).resolve().parent.parent)
                cfg = AlcCfg(str(_Path(backend_dir) / "alembic.ini"))
                alc_cmd.upgrade(cfg, "head")
                return
            else:
                raise RuntimeError(
                    "Empty database with AUTO_MIGRATE disabled. "
                    "Run: alembic upgrade head"
                )
        elif current != head:
            if _auto_migrate_enabled():
                from alembic.config import Config as AlcCfg

                from alembic import command as alc_cmd

                backend_dir = str(_Path(__file__).resolve().parent.parent)
                cfg = AlcCfg(str(_Path(backend_dir) / "alembic.ini"))
                backup_path = _backup_before_migration(db_path, current, head)
                # Release pooled SQLite handles before Alembic and possible restore.
                await gate_engine.dispose()
                try:
                    alc_cmd.upgrade(cfg, "head")
                except Exception as migration_error:
                    try:
                        _restore_migration_backup(backup_path, db_path)
                    except Exception as restore_error:
                        raise RuntimeError(
                            "Database migration failed and snapshot restoration also failed. "
                            f"Snapshot: {backup_path}"
                        ) from restore_error
                    raise RuntimeError(
                        f"Database migration failed; restored snapshot {backup_path.name}."
                    ) from migration_error
                async with gate_engine.connect() as conn:
                    upgraded = await _current_revision(conn)
                if upgraded == head:
                    return
                _restore_migration_backup(backup_path, db_path)
                raise RuntimeError(
                    f"Database auto-migration ended at '{upgraded}', expected '{head}'; "
                    f"restored snapshot {backup_path.name}."
                )
            raise RuntimeError(
                f"Database revision '{current}' does not match code revision '{head}'. "
                f"Run: alembic upgrade head"
            )
        # current == head: OK
    finally:
        await gate_engine.dispose()


async def get_db():
    async with async_session() as session:
        yield session


async def _prune_old_records(
    data_retention_days: int,
    audit_retention_days: int,
    interval_hours: int,
) -> None:
    """Background task: periodically prune old messages, conversations, and audit logs."""
    import asyncio
    import logging

    _log = logging.getLogger("db_prune")

    while True:
        await asyncio.sleep(interval_hours * 3600)
        try:
            async with session_scope() as session:
                now = datetime.now(UTC)

                # Prune audit_logs
                audit_cutoff = int(now.timestamp()) - audit_retention_days * 86400
                audit_result = await session.execute(
                    sa_text("DELETE FROM audit_logs WHERE CAST(created_at AS INTEGER) < :cutoff"),
                    {"cutoff": audit_cutoff},
                )
                audit_deleted = audit_result.rowcount

                # Prune messages (cascades to message_sources)
                msg_cutoff = (now - timedelta(days=data_retention_days)).isoformat()
                msg_result = await session.execute(
                    sa_text("DELETE FROM messages WHERE created_at < :cutoff"),
                    {"cutoff": msg_cutoff},
                )
                msg_deleted = msg_result.rowcount

                # Prune orphaned conversations (no messages left)
                conv_result = await session.execute(
                    sa_text(
                        "DELETE FROM conversations WHERE id NOT IN "
                        "(SELECT DISTINCT conversation_id FROM messages)"
                    ),
                )
                conv_deleted = conv_result.rowcount

                await session.commit()

                if audit_deleted or msg_deleted or conv_deleted:
                    _log.info(
                        "pruned: audit=%d messages=%d conversations=%d",
                        audit_deleted, msg_deleted, conv_deleted,
                    )
        except Exception:
            _log.warning("database prune failed", exc_info=True)
