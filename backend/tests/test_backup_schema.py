"""Tests for backup manifest with Alembic schema revision."""

import json
import os
import sqlite3
import tempfile
from pathlib import Path

import pytest


@pytest.fixture
def sample_db_with_revision():
    """Create a minimal SQLite DB with alembic_version table."""
    with tempfile.TemporaryDirectory() as tmp:
        db_path = Path(tmp) / "test.db"
        conn = sqlite3.connect(str(db_path))
        conn.execute("CREATE TABLE alembic_version (version_num TEXT)")
        conn.execute("INSERT INTO alembic_version VALUES ('0002')")
        conn.commit()
        conn.close()
        yield db_path


class TestBuildManifest:
    def test_manifest_includes_revision_and_sha256(self, sample_db_with_revision):
        from api.backup import _build_manifest
        upload_dir = sample_db_with_revision.parent / "uploads"
        upload_dir.mkdir()

        manifest = _build_manifest(sample_db_with_revision, upload_dir)

        assert manifest["format_version"] == 2
        assert manifest["db_schema_revision"] == "0002"
        assert "database_sha256" in manifest
        assert len(manifest["database_sha256"]) == 64  # SHA-256 hex


class TestClassifyStagedRevision:
    """Tests for _classify_staged_revision — the four-branch revision classifier."""

    @pytest.fixture(scope="class")
    def _head_and_script(self):
        from api.backup import _get_head_info
        return _get_head_info()

    def test_current_revision(self, _head_and_script):
        head, script_dir = _head_and_script
        from api.backup import _classify_staged_revision
        assert _classify_staged_revision(head, head, script_dir) == "current"

    def test_legacy_none_staged(self, _head_and_script):
        head, script_dir = _head_and_script
        from api.backup import _classify_staged_revision
        assert _classify_staged_revision(None, head, script_dir) == "legacy"

    def test_old_revision_is_ancestor(self, _head_and_script):
        head, script_dir = _head_and_script
        from api.backup import _classify_staged_revision
        assert _classify_staged_revision("0001", head, script_dir) == "old"

    def test_unknown_revision_rejected(self, _head_and_script):
        head, script_dir = _head_and_script
        from api.backup import _classify_staged_revision
        assert _classify_staged_revision("9999_nonexistent", head, script_dir) == "unknown"

    def test_get_alembic_revision_reads_db(self, sample_db_with_revision):
        from api.backup import _get_alembic_revision
        assert _get_alembic_revision(sample_db_with_revision) == "0002"

    def test_get_alembic_revision_no_table(self, tmp_path):
        from api.backup import _get_alembic_revision
        db = tmp_path / "empty.db"
        conn = sqlite3.connect(str(db))
        conn.execute("CREATE TABLE t(x)")
        conn.commit()
        conn.close()
        assert _get_alembic_revision(db) == "legacy"
