import hashlib
import os
import re
import tempfile
from collections.abc import AsyncIterator
from contextlib import suppress
from pathlib import Path

from config import settings

UPLOAD_DIR = Path(settings.upload_dir).resolve()


def _safe_path(filename: str) -> Path:
    """Resolve a file path inside UPLOAD_DIR, blocking path traversal."""
    # Strip any directory components from the filename
    safe_name = Path(filename).name
    file_path = (UPLOAD_DIR / safe_name).resolve()
    if not str(file_path).startswith(str(UPLOAD_DIR)):
        raise ValueError("Invalid filename: path traversal detected")
    return file_path


def save_upload(file_content: bytes, filename: str) -> str:
    """Save uploaded file, return stored path."""
    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    file_path = _safe_path(filename)

    # Handle duplicate filenames
    if file_path.exists():
        stem, suffix = file_path.stem, file_path.suffix
        counter = 1
        while file_path.exists():
            file_path = _safe_path(f"{stem}_{counter}{suffix}")
            counter += 1

    file_path.write_bytes(file_content)
    return str(file_path)


def create_upload_temp() -> str:
    """Create a temporary upload file on the destination filesystem."""
    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    fd, path = tempfile.mkstemp(prefix=".upload-", suffix=".tmp", dir=UPLOAD_DIR)
    os.close(fd)
    return path


def finalize_upload(temp_path: str, filename: str) -> str:
    """Atomically move a completed temporary upload to its final safe path."""
    source = Path(temp_path).resolve()
    if not source.is_relative_to(UPLOAD_DIR):
        raise ValueError("Temporary upload is outside the upload directory")

    file_path = _safe_path(filename)
    if file_path.exists():
        stem, suffix = file_path.stem, file_path.suffix
        counter = 1
        while file_path.exists():
            file_path = _safe_path(f"{stem}_{counter}{suffix}")
            counter += 1

    os.replace(source, file_path)
    return str(file_path)


def find_upload(
    filename: str,
    file_type: str,
    *,
    expected_sha256: str | None = None,
    root_dir: str | Path | None = None,
) -> str | None:
    """Locate an original upload, including collision-suffixed filenames."""
    upload_dir = Path(root_dir).resolve() if root_dir is not None else UPLOAD_DIR
    if not upload_dir.is_dir():
        return None
    exact = (upload_dir / Path(filename).name).resolve()
    if not exact.is_relative_to(upload_dir):
        return None
    if exact.is_file() and _matches_hash(exact, expected_sha256):
        return str(exact)

    stem = Path(filename).stem
    if not stem:
        return None
    for candidate in upload_dir.iterdir():
        if (
            candidate.is_file()
            and candidate.name.startswith(f"{stem}_")
            and candidate.name.endswith(file_type)
            and _matches_hash(candidate, expected_sha256)
        ):
            return str(candidate)
    return None


def _matches_hash(path: Path, expected_sha256: str | None) -> bool:
    if not expected_sha256 or len(expected_sha256) != 64:
        return True
    digest = hashlib.sha256()
    with open(path, "rb") as source:
        while chunk := source.read(64 * 1024):
            digest.update(chunk)
    return digest.hexdigest() == expected_sha256.lower()


def delete_file(file_path: str) -> None:
    """Delete uploaded file from disk. Only allows deletion within UPLOAD_DIR."""
    resolved = Path(file_path).resolve()
    if not str(resolved).startswith(str(UPLOAD_DIR)):
        return
    with suppress(FileNotFoundError):
        os.remove(file_path)


# ---------------------------------------------------------------------------
# LocalFileStorage — streaming storage backend implementing FileStorage
# ---------------------------------------------------------------------------

from storage.base import FileStorage, StagedObject, StoredObject


class LocalFileStorage(FileStorage):
    """File storage backed by a local directory.

    Enforces path safety, atomic commit via rename, and streaming I/O.
    """

    def __init__(self, root_dir: str):
        self._root = Path(root_dir).resolve()
        self._root.mkdir(parents=True, exist_ok=True)
        self._staging_dir = self._root / ".staging"
        self._staging_dir.mkdir(parents=True, exist_ok=True)

    def _safe_path(self, key: str) -> Path:
        """Resolve a storage key and verify it's within the root directory."""
        p = (self._root / key).resolve()
        if not str(p).startswith(str(self._root)):
            raise ValueError(f"Path traversal blocked: {key}")
        return p

    async def create_staging(self, filename: str) -> StagedObject:
        staged = StagedObject(filename=filename)
        staged.temp_path = str(self._staging_dir / f".upload-{staged.staging_id}")
        # Create the temp file empty
        Path(staged.temp_path).touch()
        return staged

    async def append(self, staged: StagedObject, chunk: bytes) -> None:
        with open(staged.temp_path, "ab") as f:
            f.write(chunk)
        staged.size = os.path.getsize(staged.temp_path)

    async def commit(
        self, staged: StagedObject, *, expected_sha256: str | None = None
    ) -> StoredObject:
        # Compute hash from the temp file
        sha = hashlib.sha256()
        with open(staged.temp_path, "rb") as f:
            while True:
                data = f.read(64 * 1024)
                if not data:
                    break
                sha.update(data)
        staged.sha256 = sha.hexdigest()

        if expected_sha256 and staged.sha256 != expected_sha256:
            raise ValueError(
                f"Hash mismatch: expected {expected_sha256[:16]}..., "
                f"got {staged.sha256[:16]}..."
            )

        # Generate unique storage key from hash
        storage_key = f"{staged.sha256[:2]}/{staged.sha256[2:4]}/{staged.sha256}"
        dest = self._safe_path(storage_key)
        dest.parent.mkdir(parents=True, exist_ok=True)

        # Atomic rename; if destination already exists (content-
        # addressable dedup), skip and clean up the staging file.
        if dest.exists():
            # Verify content integrity — the existing file must match.
            existing_sha = hashlib.sha256()
            with open(dest, "rb") as f:
                while True:
                    data = f.read(64 * 1024)
                    if not data:
                        break
                    existing_sha.update(data)
            if existing_sha.hexdigest() != staged.sha256:
                raise ValueError("Storage collision: same key but different content")
            os.unlink(staged.temp_path)
        else:
            os.rename(staged.temp_path, dest)

        return StoredObject(
            storage_key=storage_key,
            filename=staged.filename,
            size=staged.size,
            sha256=staged.sha256,
        )

    async def abort(self, staged: StagedObject) -> None:
        path = Path(staged.temp_path) if staged.temp_path else None
        if path and path.exists():
            path.unlink()

    async def open_read(self, storage_key: str) -> AsyncIterator[bytes]:
        path = self._safe_path(storage_key)
        with open(path, "rb") as f:
            while True:
                chunk = f.read(64 * 1024)
                if not chunk:
                    return
                yield chunk

    async def delete(self, storage_key: str) -> None:
        path = self._safe_path(storage_key)
        if path.exists():
            path.unlink()
            # Clean up empty parent dirs
            for parent in path.parents:
                if parent == self._root:
                    break
                try:
                    parent.rmdir()
                except OSError:
                    break

    async def exists(self, storage_key: str) -> bool:
        return self._safe_path(storage_key).exists()

    def local_path(self, storage_key: str) -> str:
        """Return a verified local path for zero-copy document processing."""
        path = self._safe_path(storage_key)
        if not path.is_file():
            raise FileNotFoundError(storage_key)
        return str(path)

    async def clear(self) -> None:
        """Clear known storage layouts without traversing arbitrary directories."""
        if self._staging_dir.is_dir():
            for staged in self._staging_dir.iterdir():
                if staged.is_file():
                    staged.unlink()

        key_pattern = re.compile(r"^[0-9a-f]{64}$")
        for first in self._root.iterdir():
            if first == self._staging_dir:
                continue
            if first.is_file():
                # Legacy uploads lived directly under the configured root.
                first.unlink()
                continue
            if not re.fullmatch(r"[0-9a-f]{2}", first.name):
                continue
            for second in first.iterdir():
                if not second.is_dir() or not re.fullmatch(r"[0-9a-f]{2}", second.name):
                    continue
                for stored in second.iterdir():
                    if stored.is_file() and key_pattern.fullmatch(stored.name):
                        stored.unlink()
                with suppress(OSError):
                    second.rmdir()
            with suppress(OSError):
                first.rmdir()
