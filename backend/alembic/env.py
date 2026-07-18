"""Alembic async environment for SQLite (aiosqlite)."""
import asyncio
from pathlib import Path

from alembic import context
from sqlalchemy import pool
from sqlalchemy.ext.asyncio import create_async_engine

# Import all models so Base.metadata is complete
import sys
_backend_dir = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_backend_dir))

from config import settings as _settings_module
from models.database import Base
from models import orm  # noqa: F401 — ensure all ORM models are registered

target_metadata = Base.metadata


def _get_url():
    url = _settings_module.database_url
    if not url:
        raise RuntimeError("DATABASE_URL is not set")
    return url


def run_migrations_offline():
    url = _get_url()
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection):
    context.configure(connection=connection, target_metadata=target_metadata)
    with context.begin_transaction():
        context.run_migrations()


async def run_migrations_online():
    connectable = create_async_engine(
        _get_url(),
        poolclass=pool.NullPool,
    )
    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)
    await connectable.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    # Handle both CLI (no running loop) and programmatic (inside event loop) usage
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        asyncio.run(run_migrations_online())
    else:
        # Inside a running event loop — run in a thread
        import concurrent.futures
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
            executor.submit(lambda: asyncio.run(run_migrations_online())).result()
