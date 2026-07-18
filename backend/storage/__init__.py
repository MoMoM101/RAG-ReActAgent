"""Storage module — provides a singleton LocalFileStorage instance.

Usage:
    from storage import get_storage

    storage = get_storage()
    staged = await storage.create_staging("report.pdf")
    await storage.append(staged, chunk)
    stored = await storage.commit(staged)
"""

from config import settings
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
