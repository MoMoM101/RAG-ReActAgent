"""File storage interface with streaming I/O and atomic commit."""
import hashlib
import os
import uuid
from abc import abstractmethod
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import AsyncIterator, Protocol


@dataclass
class StagedObject:
    """An in-progress file upload being assembled."""
    staging_id: str = field(default_factory=lambda: uuid.uuid4().hex)
    filename: str = ""
    temp_path: str = ""
    size: int = 0
    sha256: str = ""


@dataclass
class StoredObject:
    """A committed file in storage."""
    storage_key: str
    filename: str
    size: int
    sha256: str


class FileStorage(Protocol):
    """Storage backend for document files.

    Implementations must support streaming I/O (not whole-file-in-memory),
    atomic commit with hash verification, and path traversal safety.
    """

    @abstractmethod
    async def create_staging(self, filename: str) -> StagedObject:
        """Create a staging slot for an upload. Returns a StagedObject."""
        ...

    @abstractmethod
    async def append(self, staged: StagedObject, chunk: bytes) -> None:
        """Append a chunk to a staged upload."""
        ...

    @abstractmethod
    async def commit(
        self, staged: StagedObject, *, expected_sha256: str | None = None
    ) -> StoredObject:
        """Finalize a staged upload. Verifies hash if provided. Atomically moves to permanent storage."""
        ...

    @abstractmethod
    async def abort(self, staged: StagedObject) -> None:
        """Discard a staged upload and clean up temp files."""
        ...

    async def open_read(self, storage_key: str) -> AsyncIterator[bytes]:
        """Open a stored file for streaming read. Yields chunks."""
        ...

    @abstractmethod
    async def delete(self, storage_key: str) -> None:
        """Delete a stored file. Idempotent — no error if already missing."""
        ...

    @abstractmethod
    async def exists(self, storage_key: str) -> bool:
        """Check if a storage key exists."""
        ...
