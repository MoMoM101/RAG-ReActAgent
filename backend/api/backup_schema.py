"""Backup manifest, restore data types, and Alembic compatibility checks."""

from __future__ import annotations

import asyncio
import hashlib
import logging
import sqlite3
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

from fastapi import HTTPException

from config import settings

if TYPE_CHECKING:
    from alembic.script import ScriptDirectory

logger = logging.getLogger(__name__)
BACKUP_FORMAT_VERSION = 2


@dataclass
class RestoreTarget:
    database_path: Path
    upload_dir: Path
    qdrant_collection: str
    staging: bool = False


@dataclass
class RestoreDocument:
    id: str
    filename: str
    file_type: str
    file_hash: str
    storage_key: str = ""
    raw_text: str = ""
    chunk_count: int = 0

    @classmethod
    def from_row(cls, row: sqlite3.Row) -> RestoreDocument:
        columns = set(row.keys())
        return cls(
            id=row["id"],
            filename=row["filename"],
            file_type=row["file_type"] or ".txt",
            file_hash=row["file_hash"] or "",
            storage_key=row["storage_key"] or "" if "storage_key" in columns else "",
            raw_text=row["raw_text"] or "",
            chunk_count=row["chunk_count"] or 0,
        )


def resolve_document_upload(
    uploads_dir: Path,
    filename: str,
    storage_key: str | None,
) -> Path:
    """Resolve content-addressed uploads with a flat-layout fallback."""
    root = uploads_dir.resolve()
    if storage_key:
        candidate = (root / storage_key).resolve()
        if candidate.is_relative_to(root) and candidate.is_file():
            return candidate
    return root / Path(filename).name


def git_commit() -> str:
    """Return the current short git commit or ``unknown``."""
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        return result.stdout.strip() if result.returncode == 0 else "unknown"
    except Exception:
        return "unknown"


def get_alembic_revision(db_path: Path) -> str:
    conn = sqlite3.connect(str(db_path))
    try:
        row = conn.execute("SELECT version_num FROM alembic_version").fetchone()
        return row[0] if row else "unknown"
    except sqlite3.OperationalError:
        return "legacy"
    finally:
        conn.close()


def get_head_info() -> tuple[str, ScriptDirectory]:
    from alembic.config import Config as AlembicConfig
    from alembic.script import ScriptDirectory

    backend_dir = Path(__file__).resolve().parent.parent
    config = AlembicConfig(str(backend_dir / "alembic.ini"))
    config.set_main_option("script_location", str(backend_dir / "alembic"))
    scripts = ScriptDirectory.from_config(config)
    heads = scripts.get_heads()
    return (heads[0] if heads else "unknown", scripts)


def classify_staged_revision(
    staged_revision: str | None,
    head: str,
    script_dir: ScriptDirectory,
) -> str:
    if staged_revision is None:
        return "legacy"
    if staged_revision == head:
        return "current"
    try:
        script_dir.get_revision(staged_revision)
    except Exception:
        return "unknown"
    try:
        list(script_dir.iterate_revisions(head, staged_revision))
        return "old"
    except Exception:
        return "future"


async def migrate_staged_db(db_path: Path, head_revision: str) -> None:
    from alembic.config import Config as AlembicConfig

    from alembic import command

    backend_dir = Path(__file__).resolve().parent.parent
    config = AlembicConfig(str(backend_dir / "alembic.ini"))
    config.set_main_option("script_location", str(backend_dir / "alembic"))
    config.set_main_option(
        "sqlalchemy.url",
        f"sqlite+aiosqlite:///{db_path.as_posix()}",
    )
    try:
        await asyncio.to_thread(command.upgrade, config, "head")
    except Exception as exc:
        raise RuntimeError(f"staged migration failed: {exc}") from exc

    revision = get_alembic_revision(db_path)
    if revision != head_revision:
        raise RuntimeError(f"staged migration verification failed: expected revision {head_revision}, got {revision}")


def build_manifest(db_path: Path, upload_dir: Path) -> dict[str, Any]:
    manifest: dict[str, Any] = {
        "format_version": BACKUP_FORMAT_VERSION,
        "schema_version": BACKUP_FORMAT_VERSION,
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "git_commit": git_commit(),
        "collection_name": settings.qdrant_active_collection or settings.qdrant_collection,
        "db_schema_revision": get_alembic_revision(db_path),
        "database_sha256": hashlib.sha256(db_path.read_bytes()).hexdigest(),
        "embedding": {
            "provider": settings.embedding_provider,
            "model": settings.embedding_model,
            "dimension": settings.embedding_dim,
        },
        "chunking": {
            "size": settings.chunk_size,
            "overlap": settings.chunk_overlap,
        },
        "bm25": {"schema_version": 1, "tokenizer": "jieba"},
        "ocr": {"enabled": settings.ocr_enabled},
        "rerank": {
            "enabled": settings.rerank_enabled,
            "model": settings.rerank_model,
        },
        "files": cast(dict[str, Any], {}),
    }

    def hash_file(path: Path) -> str:
        return hashlib.sha256(path.read_bytes()).hexdigest()

    manifest["files"]["rag_agent.db"] = {
        "sha256": hash_file(db_path),
        "size": db_path.stat().st_size,
    }
    if upload_dir.exists():
        for path in sorted(upload_dir.rglob("*")):
            if path.is_file():
                relative = str(path.relative_to(upload_dir.parent))
                manifest["files"][relative] = {
                    "sha256": hash_file(path),
                    "size": path.stat().st_size,
                }
    return manifest


def verify_manifest(restore_dir: Path, manifest: dict) -> None:
    for relative, info in manifest.get("files", {}).items():
        path = restore_dir / relative
        if not path.exists() or not path.is_file():
            raise HTTPException(400, f"备份文件缺少: {relative}")
        actual = hashlib.sha256(path.read_bytes()).hexdigest()
        if actual != info.get("sha256"):
            raise HTTPException(400, f"备份文件校验失败: {relative}")


def validate_manifest_compatibility(manifest: dict) -> None:
    issues: list[str] = []
    backup_embedding = manifest.get("embedding", {})
    if backup_embedding:
        dimension = backup_embedding.get("dimension", 0)
        if dimension and dimension != settings.embedding_dim:
            issues.append(
                f"Embedding 维度不兼容: 备份={dimension}, 当前={settings.embedding_dim}。"
                "请使用相同 embedding 模型或手动执行全量重建。"
            )

    chunking = manifest.get("chunking", {})
    if chunking:
        if chunking.get("size", 0) != settings.chunk_size:
            logger.warning(
                "manifest chunk_size mismatch: backup=%d, current=%d",
                chunking.get("size"),
                settings.chunk_size,
            )
        if chunking.get("overlap", 0) != settings.chunk_overlap:
            logger.warning(
                "manifest chunk_overlap mismatch: backup=%d, current=%d",
                chunking.get("overlap"),
                settings.chunk_overlap,
            )

    bm25 = manifest.get("bm25", {})
    if bm25 and bm25.get("schema_version", 1) != 1:
        issues.append(f"BM25 schema 版本不兼容: 备份={bm25.get('schema_version')}, 当前=1")

    files = manifest.get("files", {})
    if files and not any(key.startswith("uploads/") for key in files):
        logger.warning("manifest has no upload files; restore will skip uploads")

    if issues:
        raise HTTPException(
            400,
            "备份兼容性检查失败:\n" + "\n".join(f"  - {issue}" for issue in issues),
        )
