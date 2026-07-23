"""Safe archive extraction with path traversal and resource protection."""

import io
import logging
import os
import tarfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath, PureWindowsPath

logger = logging.getLogger(__name__)

SAFE_TYPES = {tarfile.REGTYPE, tarfile.AREGTYPE, tarfile.DIRTYPE}


@dataclass(frozen=True)
class ArchiveLimits:
    upload_bytes: int = 512 * 1024 * 1024       # 512 MB
    extracted_bytes: int = 2 * 1024 * 1024 * 1024  # 2 GB
    member_count: int = 10_000
    single_member_bytes: int = 512 * 1024 * 1024  # 512 MB


def _is_within(destination: Path, resolved: str) -> bool:
    """Check that *resolved* path is inside *destination* (or equal)."""
    try:
        dest_resolved = str(destination.resolve())
    except Exception:
        return False
    dest_parts = dest_resolved.rstrip(os.sep).split(os.sep)
    target_parts = resolved.rstrip(os.sep).split(os.sep)
    return target_parts[:len(dest_parts)] == dest_parts


class ArchiveSecurityError(ValueError):
    """Raised when an archive member fails security validation."""


def validate_tar_members(
    tar: tarfile.TarFile,
    destination: Path,
    limits: ArchiveLimits,
) -> list[tarfile.TarInfo]:
    """Validate every member in the tar and return the sanitised list.

    Rejects: absolute paths, parent-directory escapes, symlinks,
    hardlinks, device files, FIFOs, and members exceeding size/count limits.
    """
    members = tar.getmembers()
    if len(members) > limits.member_count:
        raise ArchiveSecurityError(
            f"archive contains {len(members)} members, limit is {limits.member_count}"
        )

    declared_bytes = 0
    valid: list[tarfile.TarInfo] = []

    for m in members:
        # Reject non-regular / non-directory types (symlink, hardlink, device, fifo, etc.)
        if m.type not in SAFE_TYPES:
            raise ArchiveSecurityError(
                f"unsupported file type {m.type!r} for member {m.name!r}"
            )

        # Tar member names are POSIX-style, but hostile archives may contain
        # Windows drive/UNC paths or backslashes. Reject those forms explicitly
        # so validation behaves identically on Linux, macOS, and Windows.
        portable_name = m.name.replace("\\", "/")
        posix_name = PurePosixPath(portable_name)
        windows_name = PureWindowsPath(m.name)
        if (
            posix_name.is_absolute()
            or windows_name.is_absolute()
            or bool(windows_name.drive)
            or ".." in posix_name.parts
        ):
            raise ArchiveSecurityError(
                f"member {m.name!r} escapes destination directory"
            )

        resolved = os.path.normpath(str(destination / portable_name))
        if not _is_within(destination, resolved):
            raise ArchiveSecurityError(
                f"member {m.name!r} escapes destination directory"
            )

        if m.size > limits.single_member_bytes:
            raise ArchiveSecurityError(
                f"member {m.name!r} size {m.size} exceeds single-member limit"
            )

        declared_bytes += m.size
        if declared_bytes > limits.extracted_bytes:
            raise ArchiveSecurityError(
                f"total declared size exceeds extraction limit of {limits.extracted_bytes}"
            )

        valid.append(m)

    return valid


class _SizeLimitedReader(io.RawIOBase):
    """Wrap a file-like object, capping reads to *max_bytes*."""

    def __init__(self, stream, max_bytes: int):
        self._stream = stream
        self._remaining = max_bytes
        self._total = 0

    def readable(self) -> bool:
        return True

    def readinto(self, b) -> int:
        if self._remaining <= 0:
            return 0
        chunk = self._stream.read(min(len(b), self._remaining))
        if not chunk:
            return 0
        n = len(chunk)
        self._remaining -= n
        self._total += n
        b[:n] = chunk
        return n

    def read(self, size=-1) -> bytes:
        if size is None or size < 0:
            size = self._remaining
        return super().read(min(size, self._remaining))


def _chunked_reader(upload, max_bytes: int):
    """Yield bytes from *upload* in chunks, raising if total exceeds limit."""
    total = 0
    while True:
        chunk = upload.read(io.DEFAULT_BUFFER_SIZE)
        if not chunk:
            break
        total += len(chunk)
        if total > max_bytes:
            raise ArchiveSecurityError(
                f"upload size exceeds limit of {max_bytes} bytes"
            )
        yield chunk


def read_upload_chunked(upload, max_bytes: int) -> bytes:
    """Read entire upload in chunks, enforcing size cap."""
    buf = io.BytesIO()
    for chunk in _chunked_reader(upload, max_bytes):
        buf.write(chunk)
    return buf.getvalue()


def safe_extract_tar(
    tar: tarfile.TarFile,
    destination: Path,
    limits: ArchiveLimits | None = None,
) -> None:
    """Validate members and extract safely to *destination*.

    Uses Python 3.12 ``filter='data'`` as a second line of defence and
    explicit member validation as the primary guard.
    """
    if limits is None:
        limits = ArchiveLimits()

    destination.mkdir(parents=True, exist_ok=True)
    validated = validate_tar_members(tar, destination, limits)

    # Python 3.12+ filter='data' strips high-risk members; we pair it with
    # our own exhaustive validation above so the combination is robust.
    if hasattr(tarfile, "data_filter"):
        tar.extractall(destination, members=validated, filter="data")
    else:
        tar.extractall(destination, members=validated)


def open_tar_safe(fileobj, mode: str, limits: ArchiveLimits | None = None):
    """Open *fileobj* as a tarfile with size-limited reads.

    Returns ``(tarfile.TarFile, bytes_consumed)``.
    """
    if limits is None:
        limits = ArchiveLimits()

    wrapped = _SizeLimitedReader(fileobj, limits.upload_bytes)
    tar = tarfile.open(fileobj=wrapped, mode=mode)  # type: ignore[call-overload]  # noqa: SIM115
    return tar, wrapped._total
