# backend/tests/test_backup_schema.py
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


class TestValidateRestoreRevision:
    def test_same_revision_accepted(self):
        from api.backup import _validate_restore_revision
        _validate_restore_revision("0002", "0002")  # Should not raise

    def test_older_revision_accepted(self):
        from api.backup import _validate_restore_revision
        _validate_restore_revision("0001", "0002")  # Should not raise

    def test_newer_revision_rejected(self):
        from api.backup import _validate_restore_revision
        with pytest.raises(ValueError, match="newer"):
            _validate_restore_revision("0003", "0002")

    def test_legacy_no_revision_accepted(self):
        from api.backup import _validate_restore_revision
        _validate_restore_revision(None, "0002")  # Should not raise

    def test_legacy_revision_string_accepted(self):
        from api.backup import _validate_restore_revision
        _validate_restore_revision("legacy", "0002")  # non-numeric, should accept
