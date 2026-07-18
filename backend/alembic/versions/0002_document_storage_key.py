"""Add storage_key column to documents and backfill from existing file paths.

Revision ID: 0002
Revises: 0001
Create Date: 2026-07-18
"""
import hashlib
import os
from pathlib import Path
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0002"
down_revision: Union[str, None] = "0001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("documents", sa.Column("storage_key", sa.String(), nullable=True))
    op.create_index("idx_documents_storage_key", "documents", ["storage_key"])

    # Backfill: try to locate existing files by filename and compute storage_key
    conn = op.get_bind()
    rows = conn.execute(
        sa.text("SELECT id, filename, file_size, file_hash FROM documents WHERE storage_key IS NULL")
    ).fetchall()

    upload_dir = os.environ.get("UPLOAD_DIR", "./data/uploads")

    for row in rows:
        doc_id, filename, file_size, file_hash = row
        if not filename:
            continue

        upload_path = Path(upload_dir)
        matched = _find_file(upload_path, str(filename), file_size)

        if matched is not None:
            file_sha = _sha256_file(matched)
            if file_hash and str(file_hash) != file_sha:
                continue  # Hash mismatch -- skip

            storage_key = f"{file_sha[:2]}/{file_sha[2:4]}/{file_sha}"
            conn.execute(
                sa.text("UPDATE documents SET storage_key = :key WHERE id = :id"),
                {"key": storage_key, "id": doc_id},
            )


def downgrade() -> None:
    op.drop_index("idx_documents_storage_key", table_name="documents")
    op.drop_column("documents", "storage_key")


def _find_file(base: Path, filename: str, file_size) -> Path | None:
    """Find a file by filename in upload directory, optionally matching size."""
    candidates = list(base.rglob(filename))
    for cand in candidates:
        if not cand.is_file():
            continue
        if file_size is not None:
            try:
                if cand.stat().st_size != int(file_size):
                    continue
            except (ValueError, TypeError):
                pass
        return cand
    return None


def _sha256_file(path: Path) -> str:
    sha = hashlib.sha256()
    with open(path, "rb") as f:
        while True:
            data = f.read(64 * 1024)
            if not data:
                break
            sha.update(data)
    return sha.hexdigest()
