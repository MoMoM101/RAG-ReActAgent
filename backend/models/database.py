from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

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
        import os as _os
        if _os.getenv("AUTO_MIGRATE", "").lower() in ("1", "true", "yes"):
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
    return _os.getenv("AUTO_MIGRATE", "").lower() in ("1", "true", "yes")


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
                if _auto_migrate_enabled():
                    # Dev/test: existing DB without revision — stamp at head
                    from alembic import command as alc_cmd
                    from alembic.config import Config as AlcCfg

                    backend_dir = str(_Path(__file__).resolve().parent.parent)
                    cfg = AlcCfg(str(_Path(backend_dir) / "alembic.ini"))
                    alc_cmd.stamp(cfg, "head")
                    return
                raise RuntimeError(
                    "Database exists but has no Alembic revision. "
                    "Run the legacy database adoption procedure: "
                    "1) backup, 2) compute fingerprint, 3) stamp if matching."
                )
            elif _auto_migrate_enabled():
                # Dev/test: auto-migrate empty DB

                from alembic import command as alc_cmd
                from alembic.config import Config as AlcCfg

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
