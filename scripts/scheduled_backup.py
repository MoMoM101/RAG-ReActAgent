#!/usr/bin/env python3
"""Scheduled backup script for RAG Agent.

Creates a tar.gz backup of the SQLite database, uploaded files, and Qdrant
collection pointers.  Supports retention-based rotation.

Usage:
    python scripts/scheduled_backup.py                          # use defaults
    python scripts/scheduled_backup.py --retention-days 14      # keep 14 days
    python scripts/scheduled_backup.py --backup-dir /mnt/backups # remote target

Suitable for cron / Task Scheduler.
"""

import argparse
import hashlib
import json
import os
import sqlite3
import tarfile
import time
from datetime import datetime, timezone
from pathlib import Path


def _resolve(path: str) -> Path:
    return Path(path).resolve()


def _checkpoint_db(db_path: Path) -> None:
    """Truncate WAL into main DB file for a consistent backup."""
    conn = sqlite3.connect(str(db_path))
    try:
        conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    finally:
        conn.close()


def _build_manifest(db_path: Path, upload_dir: Path) -> dict:
    """Build a backup manifest with revision, checksums, and file inventory."""
    # Compute DB SHA-256
    db_sha256 = hashlib.sha256(db_path.read_bytes()).hexdigest()

    # Read Alembic revision
    try:
        conn = sqlite3.connect(str(db_path))
        row = conn.execute("SELECT version_num FROM alembic_version").fetchone()
        db_revision = row[0] if row else "unknown"
        conn.close()
    except (sqlite3.OperationalError, Exception):
        db_revision = "legacy"

    # Inventory of upload files
    files: dict[str, dict] = {}
    if upload_dir.exists():
        for f in sorted(upload_dir.rglob("*")):
            if f.is_file() and ".staging" not in str(f):
                rel = str(f.relative_to(upload_dir)).replace("\\", "/")
                files[rel] = {
                    "size": f.stat().st_size,
                    "sha256": hashlib.sha256(f.read_bytes()).hexdigest(),
                }

    return {
        "format_version": 2,
        "created_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "db_schema_revision": db_revision,
        "database_sha256": db_sha256,
        "files": files,
    }


def _create_backup(db_path: Path, upload_dir: Path, qdrant_path: Path, dest: Path) -> str:
    """Create a tar.gz backup. Returns the SHA-256 of the archive."""
    dest.parent.mkdir(parents=True, exist_ok=True)

    with tarfile.open(dest, "w:gz") as tar:
        # Database
        if db_path.exists():
            tar.add(str(db_path), arcname="rag_agent.db")

        # Uploads
        if upload_dir.exists():
            tar.add(str(upload_dir), arcname="uploads", recursive=True)

        # Qdrant collection pointers
        ptr_file = qdrant_path / "active_collections.json"
        if ptr_file.exists():
            tar.add(str(ptr_file), arcname="active_collections.json")

    sha = hashlib.sha256()
    with open(dest, "rb") as f:
        while True:
            data = f.read(64 * 1024)
            if not data:
                break
            sha.update(data)
    return sha.hexdigest()


def _rotate_backups(backup_dir: Path, retention_days: int) -> list[str]:
    """Delete backup files older than retention_days. Returns list of deleted files."""
    cutoff = time.time() - retention_days * 86400
    deleted: list[str] = []

    for f in sorted(backup_dir.glob("backup-*.tar.gz")):
        if f.stat().st_mtime < cutoff:
            stem = str(f)[:-len(".tar.gz")]  # strip .tar.gz suffix
            for ext in (".tar.gz.sha256", ".json"):
                companion = Path(stem + ext)
                if companion.exists():
                    companion.unlink()
                    deleted.append(str(companion))
            deleted.append(str(f))
            f.unlink()

    return deleted


def main():
    backend_dir = Path(__file__).resolve().parent.parent / "backend"
    data_dir = backend_dir / "data"

    parser = argparse.ArgumentParser(description="RAG Agent scheduled backup")
    parser.add_argument("--backup-dir", default=str(data_dir / "backups"))
    parser.add_argument("--retention-days", type=int, default=7)
    parser.add_argument("--db-path", default=str(data_dir / "rag_agent.db"))
    parser.add_argument("--upload-dir", default=str(data_dir / "uploads"))
    parser.add_argument("--qdrant-path", default=str(data_dir / "qdrant2"))
    args = parser.parse_args()

    backup_dir = _resolve(args.backup_dir)
    db_path = _resolve(args.db_path)
    upload_dir = _resolve(args.upload_dir)
    qdrant_path = _resolve(args.qdrant_path)

    if not db_path.exists():
        print(f"ERROR: Database not found: {db_path}")
        return 1

    ts = datetime.now().strftime("%Y-%m-%dT%H%M%S")
    archive = backup_dir / f"backup-{ts}.tar.gz"
    manifest_file = backup_dir / f"backup-{ts}.json"
    checksum_file = backup_dir / f"backup-{ts}.tar.gz.sha256"

    print(f"[{ts}] Starting backup to {archive}")

    # 1. Checkpoint WAL
    _checkpoint_db(db_path)

    # 2. Build manifest
    manifest = _build_manifest(db_path, upload_dir)
    backup_dir.mkdir(parents=True, exist_ok=True)
    manifest_file.write_text(json.dumps(manifest, indent=2, ensure_ascii=False))

    # 3. Create archive
    sha = _create_backup(db_path, upload_dir, qdrant_path, archive)
    checksum_file.write_text(f"{sha}  {archive.name}\n")

    size_mb = archive.stat().st_size / (1024 * 1024)
    print(f"[{ts}] Backup complete: {archive.name} ({size_mb:.1f} MB, sha256={sha[:16]}...)")

    # 4. Rotate old backups
    deleted = _rotate_backups(backup_dir, args.retention_days)
    if deleted:
        print(f"[{ts}] Rotated {len(deleted)} old backup files")
    elif args.retention_days > 0:
        print(f"[{ts}] No backups to rotate (retention={args.retention_days}d)")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
