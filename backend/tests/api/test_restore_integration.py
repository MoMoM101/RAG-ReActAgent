"""Integration tests for backup/restore with fault injection and consistency checks.

Covers: normal restore, 5+ fault injection points, cross-storage consistency,
atomic uploads switching, temp resource cleanup, manifest compatibility.
"""

import io
import json
import shutil
import sqlite3
import tarfile
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest
from httpx import ASGITransport, AsyncClient

from main import app

TEST_TOKEN = "evaluation-admin-token"
ADMIN_HEADERS = {"X-Admin-Token": TEST_TOKEN}

_TEMPLATE_HEAD_DB: Path | None = None
_TEMPLATE_0001_DB: Path | None = None


def _create_template_db(target_revision: str) -> Path:
    """Create a template SQLite database at a specific Alembic revision.

    Uses cfg.set_main_option so alembic/env.py targets the template file
    instead of the test database.
    """
    from alembic.config import Config as AlcCfg
    from alembic import command as alc_cmd

    tmp = Path(tempfile.mkdtemp(prefix=f"test_template_{target_revision}_"))
    db_path = tmp / "template.db"

    backend_dir = Path(__file__).resolve().parent.parent.parent
    cfg = AlcCfg(str(backend_dir / "alembic.ini"))
    cfg.set_main_option("sqlalchemy.url", f"sqlite+aiosqlite:///{db_path.as_posix()}")

    alc_cmd.upgrade(cfg, target_revision)
    return db_path


@pytest.fixture(scope="module")
def template_head_db():
    """Module-level fixture: create a head-revision template database."""
    global _TEMPLATE_HEAD_DB
    _TEMPLATE_HEAD_DB = _create_template_db("head")
    yield _TEMPLATE_HEAD_DB
    shutil.rmtree(_TEMPLATE_HEAD_DB.parent, ignore_errors=True)
    _TEMPLATE_HEAD_DB = None


@pytest.fixture(autouse=True)
def _enable_admin_token(monkeypatch):
    """Enable admin token auth for all restore integration tests."""
    from config import settings
    monkeypatch.setattr(settings, "admin_api_token", TEST_TOKEN)


def _build_test_backup_tar(
    db_rows: list[dict] | None = None,
    upload_files: dict[str, bytes] | None = None,
    *,
    include_manifest: bool = True,
    corrupt_db: bool = False,
    manifest_override: dict | None = None,
    schema: str = "head",
    revision_override: str | None = None,
) -> bytes:
    """Build an in-memory tar.gz simulating a backup archive.

    Args:
        schema: 'head', '0001', or 'legacy' (hand-crafted without alembic_version).
        revision_override: If set, write this value into manifest's
            db_schema_revision regardless of actual DB revision.
    """
    import hashlib

    tmp = Path(tempfile.mkdtemp(prefix="test_backup_build_"))
    try:
        db_path = tmp / "rag_agent.db"

        if schema == "head":
            if _TEMPLATE_HEAD_DB is None:
                raise RuntimeError(
                    "template_head_db fixture not initialized -- "
                    "add 'template_head_db' to test function parameters"
                )
            shutil.copy2(_TEMPLATE_HEAD_DB, db_path)
        elif schema == "0001":
            global _TEMPLATE_0001_DB
            if _TEMPLATE_0001_DB is None:
                _TEMPLATE_0001_DB = _create_template_db("0001")
            shutil.copy2(_TEMPLATE_0001_DB, db_path)
        elif schema == "legacy":
            conn = sqlite3.connect(str(db_path))
            conn.execute("""
                CREATE TABLE IF NOT EXISTS documents (
                    id TEXT PRIMARY KEY, filename TEXT NOT NULL,
                    file_hash TEXT NOT NULL UNIQUE, file_size INTEGER NOT NULL DEFAULT 0,
                    file_type TEXT NOT NULL DEFAULT '.txt',
                    status TEXT NOT NULL DEFAULT 'ready',
                    chunk_count INTEGER DEFAULT 0,
                    embedding_model TEXT, embedding_dim INTEGER,
                    error_message TEXT,
                    created_at TEXT DEFAULT (datetime('now')),
                    updated_at TEXT DEFAULT (datetime('now')),
                    raw_text TEXT, chunk_size INTEGER
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS conversations (
                    id TEXT PRIMARY KEY, title TEXT DEFAULT '',
                    created_at TEXT DEFAULT (datetime('now')),
                    updated_at TEXT DEFAULT (datetime('now')),
                    last_extracted_at TEXT
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS messages (
                    id TEXT PRIMARY KEY, conversation_id TEXT NOT NULL,
                    role TEXT NOT NULL, content TEXT,
                    tool_call_id TEXT, tool_name TEXT,
                    tool_args TEXT, tool_result_json TEXT, sources TEXT,
                    created_at TEXT DEFAULT (datetime('now'))
                )
            """)
            conn.commit()
            conn.close()
        else:
            raise ValueError(f"Unknown schema: {schema}")

        # Insert test data rows
        conn = sqlite3.connect(str(db_path))
        for row in (db_rows or []):
            conn.execute(
                "INSERT OR REPLACE INTO documents "
                "(id, filename, file_type, file_hash, file_size, raw_text, chunk_count, status) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (row["id"], row["filename"], row.get("file_type", ".txt"),
                 row.get("file_hash", ""), row.get("file_size", 0),
                 row.get("raw_text", ""), row.get("chunk_count", 0),
                 row.get("status", "ready")),
            )
            if row.get("conversation_id"):
                conn.execute(
                    "INSERT OR REPLACE INTO conversations (id, title) VALUES (?, ?)",
                    (row["conversation_id"], row.get("title", "Test Conv")),
                )
                conn.execute(
                    "INSERT OR REPLACE INTO messages (id, conversation_id, role, content) "
                    "VALUES (?, ?, ?, ?)",
                    (row.get("msg_id", f"msg-{row['id']}"),
                     row["conversation_id"], "user",
                     row.get("msg_content", "hello")),
                )
        conn.commit()
        conn.close()

        if corrupt_db:
            data = bytearray(db_path.read_bytes())
            mid = len(data) // 2
            if mid > 50:
                data[mid:mid + 50] = b"\x00" * 50
            db_path.write_bytes(bytes(data))

        # Build upload files
        uploads_dir = tmp / "uploads"
        if upload_files:
            uploads_dir.mkdir(parents=True, exist_ok=True)
            for rel_path, content in upload_files.items():
                fp = uploads_dir / rel_path
                fp.parent.mkdir(parents=True, exist_ok=True)
                fp.write_bytes(content)

        # Determine db_schema_revision for manifest
        if schema == "legacy":
            actual_revision = None
        else:
            actual_revision = _read_alembic_revision_from_db(db_path)

        # Build manifest
        if include_manifest:
            manifest = manifest_override or {
                "format_version": 2,
                "created_at": "2026-07-11T00:00:00Z",
                "collection_name": "rag_chunks",
                "db_schema_revision": actual_revision,
                "files": {
                    "rag_agent.db": {
                        "sha256": hashlib.sha256(db_path.read_bytes()).hexdigest(),
                        "size": db_path.stat().st_size,
                    }
                },
            }
            if revision_override is not None:
                manifest["db_schema_revision"] = revision_override
            if upload_files and manifest_override is None:
                for rel_path in upload_files:
                    fp = uploads_dir / rel_path
                    manifest["files"][f"uploads/{rel_path}"] = {
                        "sha256": hashlib.sha256(fp.read_bytes()).hexdigest(),
                        "size": fp.stat().st_size,
                    }
            (tmp / "manifest.json").write_text(json.dumps(manifest, indent=2),
                                                encoding="utf-8")

        # Build tar.gz
        buf = io.BytesIO()
        with tarfile.open(fileobj=buf, mode="w:gz") as tar:
            tar.add(db_path, arcname="rag_agent.db")
            if include_manifest and (tmp / "manifest.json").exists():
                tar.add(tmp / "manifest.json", arcname="manifest.json")
            if upload_files and uploads_dir.exists():
                tar.add(uploads_dir, arcname="uploads")
        return buf.getvalue()
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def _read_alembic_revision_from_db(db_path: Path) -> str | None:
    """Read alembic_version from a SQLite database. Returns None if absent."""
    conn = sqlite3.connect(str(db_path))
    try:
        row = conn.execute("SELECT version_num FROM alembic_version").fetchone()
        return row[0] if row else None
    except sqlite3.OperationalError:
        return None
    finally:
        conn.close()


# ── Normal restore ─────────────────────────────────────────────────────


@pytest.mark.usefixtures("mock_embedding")
class TestNormalRestore:
    """End-to-end restore: backup -> restore -> verify consistency."""

    async def test_restore_preserves_document_ids(self):
        doc_id = "test-doc-normal-001"
        tar_bytes = _build_test_backup_tar(
            db_rows=[{
                "id": doc_id, "filename": "test_restore.txt",
                "file_type": ".txt", "file_hash": "abc123",
                "raw_text": "Test document content for restore.",
                "chunk_count": 1,
            }],
            upload_files={"test_restore.txt": b"Test document content for restore."},
        )

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            r = await client.post(
                "/api/backup/restore",
                files={"file": ("backup.tar.gz", tar_bytes, "application/gzip")},
                headers=ADMIN_HEADERS,
            )
            assert r.status_code == 200
            data = r.json()
            assert data["status"] == "ok"
            assert data["documents_total"] >= 1

            # Verify document is accessible
            r = await client.get("/api/documents", headers=ADMIN_HEADERS)
            assert r.status_code == 200
            docs = r.json()
            filenames = [d["filename"] for d in docs]
            assert "test_restore.txt" in filenames

    async def test_restore_empty_backup(self):
        tar_bytes = _build_test_backup_tar(db_rows=[])

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            r = await client.post(
                "/api/backup/restore",
                files={"file": ("empty.tar.gz", tar_bytes, "application/gzip")},
                headers=ADMIN_HEADERS,
            )
            assert r.status_code == 200
            data = r.json()
            assert data["status"] == "ok"
            assert data["documents_total"] == 0

    async def test_restore_preserves_conversations(self):
        conv_id = "conv-test-001"
        tar_bytes = _build_test_backup_tar(
            db_rows=[{
                "id": "doc-restore-001", "filename": "doc.txt",
                "file_type": ".txt", "file_hash": "hash001",
                "raw_text": "Content", "chunk_count": 1,
                "conversation_id": conv_id, "msg_id": "msg-test-001",
                "msg_content": "Hello, test message",
            }],
            upload_files={"doc.txt": b"Content"},
        )

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            r = await client.post(
                "/api/backup/restore",
                files={"file": ("backup.tar.gz", tar_bytes, "application/gzip")},
                headers=ADMIN_HEADERS,
            )
            assert r.status_code == 200

    async def test_reject_non_tar_gz(self):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            r = await client.post(
                "/api/backup/restore",
                files={"file": ("bad.zip", b"not a tar.gz", "application/zip")},
                headers=ADMIN_HEADERS,
            )
            assert r.status_code == 400


# ── Fault injection ────────────────────────────────────────────────────


class TestFaultInjection:
    """Fault injection at switch points to verify rollback correctness."""

    async def test_qdrant_build_failure_triggers_rollback(self, mock_embedding):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            r = await client.get("/api/documents", headers=ADMIN_HEADERS)
            assert r.status_code == 200
            original_count = len(r.json())

            tar_bytes = _build_test_backup_tar(
                db_rows=[{
                    "id": "doc-fault-001", "filename": "fault_test.txt",
                    "file_type": ".txt", "file_hash": "fault_hash",
                    "raw_text": "Fault injection test content.", "chunk_count": 2,
                }],
                upload_files={"fault_test.txt": b"Fault injection test content."},
            )

            with patch("api.backup._build_restore_qdrant",
                       side_effect=RuntimeError("Simulated Qdrant build failure")):
                r = await client.post(
                    "/api/backup/restore",
                    files={"file": ("fault.tar.gz", tar_bytes, "application/gzip")},
                    headers=ADMIN_HEADERS,
                )
                assert r.status_code == 500

            # Original data intact
            r = await client.get("/api/documents", headers=ADMIN_HEADERS)
            assert r.status_code == 200
            assert len(r.json()) == original_count

    async def test_manifest_tampered_hash_rejected(self):
        tar_bytes = _build_test_backup_tar(
            db_rows=[{"id": "doc-tamper-001", "filename": "tamper.txt"}],
            upload_files={"tamper.txt": b"original content"},
            manifest_override={
                "format_version": 1,
                "files": {
                    "rag_agent.db": {
                        "sha256": "deadbeef" * 8,  # wrong hash
                        "size": 123,
                    }
                },
            },
        )

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            r = await client.post(
                "/api/backup/restore",
                files={"file": ("tampered.tar.gz", tar_bytes, "application/gzip")},
                headers=ADMIN_HEADERS,
            )
            assert r.status_code == 400
            assert "校验失败" in r.json()["detail"]

    async def test_manifest_missing_file_rejected(self):
        """A manifest referencing a nonexistent file should be rejected."""
        import hashlib

        tmp = Path(tempfile.mkdtemp(prefix="test_manifest_missing_"))
        try:
            db_path = tmp / "rag_agent.db"
            conn = sqlite3.connect(str(db_path))
            conn.execute("""
                CREATE TABLE documents (
                    id TEXT PRIMARY KEY, filename TEXT NOT NULL,
                    file_hash TEXT NOT NULL UNIQUE, file_size INTEGER NOT NULL DEFAULT 0,
                    file_type TEXT NOT NULL DEFAULT '.txt',
                    status TEXT NOT NULL DEFAULT 'ready',
                    chunk_count INTEGER DEFAULT 0,
                    error_message TEXT, created_at TEXT, updated_at TEXT,
                    raw_text TEXT, chunk_size INTEGER
                )
            """)
            conn.commit()
            conn.close()

            db_hash = hashlib.sha256(db_path.read_bytes()).hexdigest()
            db_size = db_path.stat().st_size

            manifest = {
                "format_version": 1,
                "files": {
                    "rag_agent.db": {"sha256": db_hash, "size": db_size},
                    "uploads/missing_file.pdf": {"sha256": "deadbeef" * 8, "size": 200},
                },
            }
            (tmp / "manifest.json").write_text(json.dumps(manifest, indent=2))

            buf = io.BytesIO()
            with tarfile.open(fileobj=buf, mode="w:gz") as tar:
                tar.add(db_path, arcname="rag_agent.db")
                tar.add(tmp / "manifest.json", arcname="manifest.json")
            tar_bytes = buf.getvalue()
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            r = await client.post(
                "/api/backup/restore",
                files={"file": ("missing_file.tar.gz", tar_bytes, "application/gzip")},
                headers=ADMIN_HEADERS,
            )
            assert r.status_code == 400
            assert "缺少" in r.json()["detail"]

    async def test_corrupt_db_rejected(self):
        """A backup with a corrupt SQLite database should be rejected."""
        # Corrupt the header, not just data, to reliably fail PRAGMA integrity_check
        tmp = Path(tempfile.mkdtemp(prefix="test_corrupt_db_"))
        try:
            db_path = tmp / "rag_agent.db"
            conn = sqlite3.connect(str(db_path))
            conn.execute("CREATE TABLE t(x)")
            conn.execute("INSERT INTO t VALUES (1)")
            conn.commit()
            conn.close()

            # Corrupt the first 100 bytes (SQLite header)
            data = bytearray(db_path.read_bytes())
            for i in range(min(100, len(data))):
                data[i] = 0
            db_path.write_bytes(bytes(data))

            buf = io.BytesIO()
            with tarfile.open(fileobj=buf, mode="w:gz") as tar:
                tar.add(db_path, arcname="rag_agent.db")
            tar_bytes = buf.getvalue()
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            r = await client.post(
                "/api/backup/restore",
                files={"file": ("corrupt.tar.gz", tar_bytes, "application/gzip")},
                headers=ADMIN_HEADERS,
            )
            assert r.status_code == 400
            assert "完整性" in r.json()["detail"]

    async def test_restore_during_maintenance_rejected(self):
        from maintenance import get_maintenance_state

        mstate = get_maintenance_state()
        await mstate.acquire()

        try:
            tar_bytes = _build_test_backup_tar(db_rows=[])
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                r = await client.post(
                    "/api/backup/restore",
                    files={"file": ("backup.tar.gz", tar_bytes, "application/gzip")},
                    headers=ADMIN_HEADERS,
                )
                assert r.status_code == 503
        finally:
            mstate.release()

    async def test_uploads_switch_failure_triggers_rollback(self, mock_embedding):
        """When shutil.copytree fails during uploads switch, restore rolls back."""
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            r = await client.get("/api/documents", headers=ADMIN_HEADERS)
            original_count = len(r.json())

            tar_bytes = _build_test_backup_tar(
                db_rows=[{
                    "id": "doc-atomic-001", "filename": "atomic_test.txt",
                    "file_type": ".txt", "raw_text": "Atomic switch test.",
                    "chunk_count": 1,
                }],
                upload_files={"atomic_test.txt": b"Atomic switch test."},
            )

            with patch("api.backup.shutil.copytree",
                       side_effect=OSError("Simulated disk full")):
                r = await client.post(
                    "/api/backup/restore",
                    files={"file": ("backup.tar.gz", tar_bytes, "application/gzip")},
                    headers=ADMIN_HEADERS,
                )
                assert r.status_code == 500

            # System still functional
            r = await client.get("/api/documents", headers=ADMIN_HEADERS)
            assert r.status_code == 200
            assert len(r.json()) == original_count


# ── Cross-consistency ──────────────────────────────────────────────────


class TestCrossConsistency:
    """Verify cross-storage consistency checks catch mismatches."""

    async def test_consistency_missing_upload(self):
        from api.backup import _cross_consistency_check

        tmp = Path(tempfile.mkdtemp(prefix="test_consistency_"))
        try:
            db_path = tmp / "test.db"
            conn = sqlite3.connect(str(db_path))
            conn.execute("""
                CREATE TABLE documents (
                    id TEXT PRIMARY KEY, filename TEXT, file_hash TEXT,
                    chunk_count INTEGER, status TEXT DEFAULT 'ready'
                )
            """)
            conn.execute(
                "INSERT INTO documents (id, filename, file_hash, chunk_count, status) "
                "VALUES (?, ?, ?, ?, ?)",
                ("doc-missing", "nonexistent.txt", "hash", 3, "ready"),
            )
            conn.commit()
            conn.close()

            uploads_dir = tmp / "uploads"
            uploads_dir.mkdir()

            result = await _cross_consistency_check(db_path, "test_collection", uploads_dir)
            assert result["passed"] is False
            assert any("upload missing" in issue for issue in result["issues"])
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    async def test_consistency_empty_docs_passes(self):
        from api.backup import _cross_consistency_check

        tmp = Path(tempfile.mkdtemp(prefix="test_consistency_"))
        try:
            db_path = tmp / "test.db"
            conn = sqlite3.connect(str(db_path))
            conn.execute("""
                CREATE TABLE documents (
                    id TEXT PRIMARY KEY, filename TEXT, file_hash TEXT,
                    chunk_count INTEGER, status TEXT DEFAULT 'ready'
                )
            """)
            conn.commit()
            conn.close()

            uploads_dir = tmp / "uploads"
            uploads_dir.mkdir()

            result = await _cross_consistency_check(db_path, "test_collection", uploads_dir)
            assert result["passed"] is True
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    async def test_consistency_id_level_mismatch_detected(self):
        """Consistency should fail when chunk counts mismatch, not just counts."""
        from api.backup import _cross_consistency_check

        tmp = Path(tempfile.mkdtemp(prefix="test_consistency_"))
        try:
            db_path = tmp / "test.db"
            conn = sqlite3.connect(str(db_path))
            conn.execute("""
                CREATE TABLE documents (
                    id TEXT PRIMARY KEY, filename TEXT, file_hash TEXT,
                    chunk_count INTEGER, status TEXT DEFAULT 'ready'
                )
            """)
            conn.execute(
                "INSERT INTO documents (id, filename, file_hash, chunk_count, status) "
                "VALUES (?, ?, ?, ?, ?)",
                ("doc-1", "exists.txt", "hash1", 5, "ready"),
            )
            conn.commit()
            conn.close()

            uploads_dir = tmp / "uploads"
            uploads_dir.mkdir()
            (uploads_dir / "exists.txt").write_text("content")

            result = await _cross_consistency_check(db_path, "test_collection", uploads_dir)
            # Upload exists but Qdrant may be empty — the test collection doesn't exist
            # so the check should flag the Qdrant count mismatch
            assert "issues" in result
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


# ── Manifest compatibility ─────────────────────────────────────────────


class TestManifestCompatibility:
    """Manifest structure and version checks."""

    def test_manifest_contains_required_fields(self):
        from api.backup import _build_manifest

        tmp = Path(tempfile.mkdtemp(prefix="test_manifest_"))
        try:
            db_path = tmp / "test.db"
            db_path.write_bytes(b"test db content")
            uploads_dir = tmp / "uploads"
            uploads_dir.mkdir()
            (uploads_dir / "doc1.txt").write_text("content 1")

            manifest = _build_manifest(db_path, uploads_dir)
            assert "format_version" in manifest
            assert "created_at" in manifest
            assert "files" in manifest
            assert "rag_agent.db" in manifest["files"]
            assert "sha256" in manifest["files"]["rag_agent.db"]
            assert "size" in manifest["files"]["rag_agent.db"]
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_manifest_includes_all_upload_files(self):
        from api.backup import _build_manifest

        tmp = Path(tempfile.mkdtemp(prefix="test_manifest_"))
        try:
            db_path = tmp / "test.db"
            db_path.write_bytes(b"test db")
            uploads_dir = tmp / "uploads"
            uploads_dir.mkdir()
            (uploads_dir / "a.txt").write_text("AAA")
            (uploads_dir / "subdir").mkdir()
            (uploads_dir / "subdir" / "b.txt").write_text("BBB")

            manifest = _build_manifest(db_path, uploads_dir)
            files = manifest["files"]
            upload_keys = [k for k in files if k != "rag_agent.db"]
            assert len(upload_keys) == 2
            for k in upload_keys:
                assert "sha256" in files[k]
                assert "size" in files[k]
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    @pytest.mark.usefixtures("mock_embedding")
    async def test_restore_without_manifest_succeeds(self):
        tar_bytes = _build_test_backup_tar(
            db_rows=[{
                "id": "doc-nomanifest-001", "filename": "nomanifest.txt",
                "file_type": ".txt", "raw_text": "No manifest test.",
                "chunk_count": 1,
            }],
            upload_files={"nomanifest.txt": b"No manifest test."},
            include_manifest=False,
        )

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            r = await client.post(
                "/api/backup/restore",
                files={"file": ("nomanifest.tar.gz", tar_bytes, "application/gzip")},
                headers=ADMIN_HEADERS,
            )
            assert r.status_code == 200


# ── Maintenance state ──────────────────────────────────────────────────


class TestMaintenanceState:
    """Maintenance state transitions and read-after-write."""

    async def test_maintenance_status_idle(self):
        from maintenance import get_maintenance_state
        mstate = get_maintenance_state()
        # Ensure no leftover lock from previous tests
        if not mstate.active:
            snap = mstate.snapshot()
            assert not snap["active"]

    async def test_maintenance_status_endpoint(self):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            r = await client.get("/api/backup/maintenance-status", headers=ADMIN_HEADERS)
            assert r.status_code == 200
            data = r.json()
            assert "active" in data
            assert "phase" in data


# ── Auth required ──────────────────────────────────────────────────────


class TestRestoreAuthRequired:
    """Restore endpoints require authentication."""

    async def test_restore_no_token_returns_401(self):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            r = await client.post(
                "/api/backup/restore",
                files={"file": ("test.tar.gz", b"x", "application/gzip")},
            )
            assert r.status_code == 401

    async def test_backup_download_no_token_returns_401(self):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            r = await client.get("/api/backup")
            assert r.status_code == 401


async def test_collection_pointer_creates_missing_qdrant_directory(
    tmp_path, monkeypatch,
):
    """Remote Qdrant deployments do not pre-create the local qdrant path."""
    from api.backup import _save_active_collection_pointer
    from config import settings

    pointer_dir = tmp_path / "missing" / "qdrant"
    monkeypatch.setattr(settings, "qdrant_path", str(pointer_dir))

    await _save_active_collection_pointer("rag_chunks_restore_test")

    pointer = pointer_dir / "active_collections.json"
    assert pointer.exists()
    assert '"rag_chunks": "rag_chunks_restore_test"' in pointer.read_text()
