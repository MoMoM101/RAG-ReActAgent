"""Storage access and backend-neutral materialization helpers.

Usage:
    from storage import get_storage

    storage = get_storage()
    staged = await storage.create_staging("report.pdf")
    await storage.append(staged, chunk)
    stored = await storage.commit(staged)
"""

import os
import tempfile
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from config import settings
from storage.base import FileStorage, StagedObject
from storage.files import LocalFileStorage

_storage: LocalFileStorage | None = None


def get_storage() -> LocalFileStorage:
    """Return the singleton LocalFileStorage instance.

    Lazily initialized on first call using settings.upload_dir.
    """
    global _storage
    if _storage is None:
        _storage = LocalFileStorage(settings.upload_dir)
    return _storage


def reset_storage() -> None:
    """Test-only: reset the singleton so a fresh instance is created."""
    global _storage
    _storage = None


async def stage_path(
    storage: FileStorage,
    source_path: str,
    filename: str,
) -> StagedObject:
    """Copy an existing path into backend-managed staging using bounded reads."""
    staged = await storage.create_staging(filename)
    try:
        with open(source_path, "rb") as source:
            while chunk := source.read(64 * 1024):
                await storage.append(staged, chunk)
        return staged
    except BaseException:
        await storage.abort(staged)
        raise


@asynccontextmanager
async def materialize(
    storage: FileStorage,
    storage_key: str,
    *,
    suffix: str = "",
) -> AsyncIterator[str]:
    """Yield a local readable path for either local or remote storage backends."""
    local_path = getattr(storage, "local_path", None)
    if callable(local_path):
        yield local_path(storage_key)
        return

    fd, temp_path = tempfile.mkstemp(prefix="rag-storage-", suffix=suffix)
    os.close(fd)
    try:
        with open(temp_path, "wb") as output:
            async for chunk in storage.open_read(storage_key):
                output.write(chunk)
        yield temp_path
    finally:
        Path(temp_path).unlink(missing_ok=True)
