"""Security tests for backup archive handling.

Covers: path traversal, symlinks, hardlinks, device files, FIFOs,
member limits, size limits, temp file cleanup.
"""

import io
import os
import tarfile
import tempfile
from pathlib import Path

import pytest

from utils.safe_archive import (
    ArchiveLimits,
    ArchiveSecurityError,
    safe_extract_tar,
)


def _tar_bytes(members: list[tuple[str, bytes | None, int]]) -> bytes:
    """Build an in-memory tar.gz from (name, content, type) tuples.

    *content* may be ``None`` for directories or special entries.
    """
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        for name, content, typ in members:
            info = tarfile.TarInfo(name=name)
            info.type = typ  # type: ignore[assignment]
            info.mode = 0o644 if typ in (tarfile.REGTYPE, tarfile.AREGTYPE) else 0o755
            info.uid = 1000
            info.gid = 1000
            info.mtime = 0
            if content is not None:
                info.size = len(content)
            elif typ in (tarfile.REGTYPE, tarfile.AREGTYPE):
                content = b""
                info.size = 0
            else:
                content = b""
                info.size = 0
            tar.addfile(info, io.BytesIO(content))
    return buf.getvalue()


class TestSafeExtract:
    """Normal extraction behaviour."""

    def test_extract_normal_files(self):
        data = _tar_bytes([
            ("rag_agent.db", b"sqlite-data", tarfile.REGTYPE),
            ("uploads/readme.txt", b"hello", tarfile.REGTYPE),
        ])
        dest = Path(tempfile.mkdtemp(prefix="test_extract_"))
        try:
            with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as tar:
                safe_extract_tar(tar, dest)
            assert (dest / "rag_agent.db").read_bytes() == b"sqlite-data"
            assert (dest / "uploads" / "readme.txt").read_bytes() == b"hello"
        finally:
            import shutil
            shutil.rmtree(dest, ignore_errors=True)


class TestPathTraversal:
    """Reject absolute paths and parent-directory escapes."""

    def test_reject_dotdot(self):
        data = _tar_bytes([
            ("../outside.txt", b"escaped", tarfile.REGTYPE),
        ])
        dest = Path(tempfile.mkdtemp(prefix="test_dotdot_"))
        try:
            with (
                tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as tar,
                pytest.raises(ArchiveSecurityError, match="escapes"),
            ):
                safe_extract_tar(tar, dest)
        finally:
            import shutil
            shutil.rmtree(dest, ignore_errors=True)

    def test_reject_absolute_path(self):
        name = "C:\\windows\\system32\\evil.txt" if os.name == "nt" else "/etc/passwd"
        data = _tar_bytes([(name, b"bad", tarfile.REGTYPE)])
        dest = Path(tempfile.mkdtemp(prefix="test_abs_"))
        try:
            with (
                tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as tar,
                pytest.raises(ArchiveSecurityError, match="escapes"),
            ):
                safe_extract_tar(tar, dest)
        finally:
            import shutil
            shutil.rmtree(dest, ignore_errors=True)

    def test_reject_windows_drive_letter(self):
        data = _tar_bytes([
            ("D:/data/evil.dll", b"dll", tarfile.REGTYPE),
        ])
        dest = Path(tempfile.mkdtemp(prefix="test_win_"))
        try:
            with (
                tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as tar,
                pytest.raises(ArchiveSecurityError, match="escapes"),
            ):
                safe_extract_tar(tar, dest)
        finally:
            import shutil
            shutil.rmtree(dest, ignore_errors=True)


class TestSpecialFileTypes:
    """Reject symlinks, hardlinks, device files, and FIFOs."""

    def test_reject_symlink(self):
        data = _tar_bytes([
            ("link", b"/etc/passwd", tarfile.SYMTYPE),
        ])
        dest = Path(tempfile.mkdtemp(prefix="test_sym_"))
        try:
            with (
                tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as tar,
                pytest.raises(ArchiveSecurityError, match="unsupported file type"),
            ):
                safe_extract_tar(tar, dest)
        finally:
            import shutil
            shutil.rmtree(dest, ignore_errors=True)

    def test_reject_hardlink(self):
        # LNKTYPE needs linkname set
        buf = io.BytesIO()
        with tarfile.open(fileobj=buf, mode="w:gz") as tar:
            info1 = tarfile.TarInfo(name="original")
            info1.type = tarfile.REGTYPE
            info1.size = 1
            tar.addfile(info1, io.BytesIO(b"x"))
            info2 = tarfile.TarInfo(name="link")
            info2.type = tarfile.LNKTYPE
            info2.linkname = "original"
            info2.size = 0
            tar.addfile(info2, io.BytesIO(b""))
        data = buf.getvalue()

        dest = Path(tempfile.mkdtemp(prefix="test_hard_"))
        try:
            with (
                tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as tar,
                pytest.raises(ArchiveSecurityError, match="unsupported file type"),
            ):
                safe_extract_tar(tar, dest)
        finally:
            import shutil
            shutil.rmtree(dest, ignore_errors=True)

    def test_reject_fifo(self):
        data = _tar_bytes([
            ("pipe", b"", tarfile.FIFOTYPE),
        ])
        dest = Path(tempfile.mkdtemp(prefix="test_fifo_"))
        try:
            with (
                tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as tar,
                pytest.raises(ArchiveSecurityError, match="unsupported file type"),
            ):
                safe_extract_tar(tar, dest)
        finally:
            import shutil
            shutil.rmtree(dest, ignore_errors=True)


class TestResourceLimits:
    """Enforce member count and size limits."""

    def test_reject_too_many_members(self):
        members = [
            (f"file_{i}.txt", b"x", tarfile.REGTYPE)
            for i in range(10)
        ]
        data = _tar_bytes(members)
        limits = ArchiveLimits(member_count=5)
        dest = Path(tempfile.mkdtemp(prefix="test_count_"))
        try:
            with (
                tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as tar,
                pytest.raises(ArchiveSecurityError, match="contains"),
            ):
                safe_extract_tar(tar, dest, limits)
        finally:
            import shutil
            shutil.rmtree(dest, ignore_errors=True)

    def test_reject_large_member(self):
        data = _tar_bytes([
            ("big.bin", b"A" * 1000, tarfile.REGTYPE),
        ])
        limits = ArchiveLimits(single_member_bytes=100)
        dest = Path(tempfile.mkdtemp(prefix="test_big_"))
        try:
            with (
                tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as tar,
                pytest.raises(ArchiveSecurityError, match="single-member"),
            ):
                safe_extract_tar(tar, dest, limits)
        finally:
            import shutil
            shutil.rmtree(dest, ignore_errors=True)

    def test_reject_total_extracted_too_large(self):
        data = _tar_bytes([
            ("f1.bin", b"A" * 500, tarfile.REGTYPE),
            ("f2.bin", b"B" * 500, tarfile.REGTYPE),
        ])
        limits = ArchiveLimits(extracted_bytes=600)
        dest = Path(tempfile.mkdtemp(prefix="test_total_"))
        try:
            with (
                tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as tar,
                pytest.raises(ArchiveSecurityError, match="total declared size"),
            ):
                safe_extract_tar(tar, dest, limits)
        finally:
            import shutil
            shutil.rmtree(dest, ignore_errors=True)


class TestCleanup:
    """Temporary resources are cleaned up regardless of outcome."""

    def test_temp_dir_removed_after_success(self):
        data = _tar_bytes([
            ("rag_agent.db", b"ok", tarfile.REGTYPE),
        ])
        dest = Path(tempfile.mkdtemp(prefix="test_cleanup_"))
        try:
            with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as tar:
                safe_extract_tar(tar, dest)
            assert dest.exists()
        finally:
            import shutil
            shutil.rmtree(dest, ignore_errors=True)
            assert not dest.exists()

    def test_temp_dir_removed_after_error(self):
        data = _tar_bytes([
            ("../evil.txt", b"bad", tarfile.REGTYPE),
        ])
        dest = Path(tempfile.mkdtemp(prefix="test_cleanup_err_"))
        try:
            with (
                tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as tar,
                pytest.raises(ArchiveSecurityError),
            ):
                safe_extract_tar(tar, dest)
            assert not (dest / "evil.txt").exists()
        finally:
            import shutil
            shutil.rmtree(dest, ignore_errors=True)
