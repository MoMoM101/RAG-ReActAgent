"""Archive verification and schema preparation for backup restore."""

from __future__ import annotations

import io
import json
import logging
import sqlite3
import tarfile
from dataclasses import dataclass
from pathlib import Path

from fastapi import HTTPException, UploadFile

from api.backup_schema import (
    RestoreDocument,
    classify_staged_revision,
    get_alembic_revision,
    get_head_info,
    migrate_staged_db,
    validate_manifest_compatibility,
    verify_manifest,
)
from utils.safe_archive import (
    ArchiveLimits,
    ArchiveSecurityError,
    read_upload_chunked,
    safe_extract_tar,
)

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class PreparedRestoreArchive:
    """Verified restore inputs extracted into an isolated directory."""

    manifest: dict | None
    database_path: Path
    uploads_path: Path
    documents: list[RestoreDocument]
    document_row_count: int


def _verify_sqlite(database_path: Path) -> None:
    try:
        conn = sqlite3.connect(str(database_path))
    except sqlite3.Error as exc:
        raise HTTPException(400, f"备份数据库无法打开: {exc}") from exc
    try:
        result = conn.execute("PRAGMA integrity_check").fetchone()
        if result and result[0] != "ok":
            raise HTTPException(400, f"备份数据库完整性检查失败: {result[0]}")
    except sqlite3.Error as exc:
        raise HTTPException(400, f"备份数据库完整性检查失败: {exc}") from exc
    finally:
        conn.close()


def _read_manifest(restore_dir: Path) -> dict | None:
    manifest_path = restore_dir / "manifest.json"
    if not manifest_path.exists():
        return None
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise HTTPException(400, "备份文件 manifest.json 格式无效") from exc
    verify_manifest(restore_dir, manifest)
    validate_manifest_compatibility(manifest)
    return manifest


async def _prepare_schema(database_path: Path, manifest: dict | None) -> None:
    staged_raw = get_alembic_revision(database_path)
    staged_revision = None if staged_raw in ("legacy", "unknown") else staged_raw
    head_revision, script_dir = get_head_info()

    if manifest is not None:
        manifest_revision = manifest.get("db_schema_revision")
        if manifest_revision is not None:
            if staged_revision is None:
                raise HTTPException(
                    400,
                    f"备份 manifest 声明版本 {manifest_revision}，"
                    "但备份数据库未包含 Alembic 版本信息。"
                    "该备份可能损坏或来自旧版本应用，请通过离线流程采纳。",
                )
            if manifest_revision != staged_revision:
                raise HTTPException(
                    400,
                    f"备份 manifest 版本 ({manifest_revision}) 与数据库实际版本 ({staged_revision}) 不一致，备份文件可能损坏。",
                )

    classification = classify_staged_revision(
        staged_revision,
        head_revision,
        script_dir,
    )
    if classification == "legacy":
        raise HTTPException(
            400,
            "备份数据库不包含版本信息（legacy 格式）。"
            "legacy 备份不支持直接恢复。请通过离线采纳流程："
            "1) 提取备份中的数据库文件，2) 核对表结构指纹，"
            "3) 使用 alembic stamp 标记版本后重新打包。",
        )
    if classification in ("unknown", "future"):
        raise HTTPException(
            400,
            f"备份数据库版本 '{staged_revision}' 不被当前应用识别。请升级应用到包含此版本的新版本。",
        )
    if classification == "old":
        logger.info(
            "staged DB revision %s < head %s, running staged migration",
            staged_revision,
            head_revision,
        )
        try:
            await migrate_staged_db(database_path, head_revision)
        except RuntimeError as exc:
            raise HTTPException(400, f"备份数据库迁移失败: {exc}") from exc
        logger.info("staged migration complete, revision now at %s", head_revision)


def _read_documents(database_path: Path) -> list[RestoreDocument]:
    conn = sqlite3.connect(str(database_path))
    conn.row_factory = sqlite3.Row
    try:
        rows = list(
            conn.execute(
                "SELECT id, filename, file_type, file_hash, storage_key, "
                "raw_text, chunk_count FROM documents WHERE status = 'ready'"
            ).fetchall()
        )
    finally:
        conn.close()
    return [RestoreDocument.from_row(row) for row in rows]


async def prepare_restore_archive(
    file: UploadFile,
    restore_dir: Path,
    limits: ArchiveLimits,
) -> PreparedRestoreArchive:
    """Safely extract, verify, migrate, and load a restore archive."""
    try:
        content = read_upload_chunked(file.file, limits.upload_bytes)
    except ArchiveSecurityError as exc:
        raise HTTPException(413, str(exc)) from exc

    with tarfile.open(fileobj=io.BytesIO(content), mode="r:gz") as archive:
        try:
            safe_extract_tar(archive, restore_dir, limits)
        except ArchiveSecurityError as exc:
            raise HTTPException(400, f"备份文件安全校验失败: {exc}") from exc

    manifest = _read_manifest(restore_dir)
    database_path = restore_dir / "rag_agent.db"
    uploads_path = restore_dir / "uploads"
    if not database_path.exists():
        raise HTTPException(400, "备份文件不完整：缺少 rag_agent.db")

    _verify_sqlite(database_path)
    await _prepare_schema(database_path, manifest)
    documents = _read_documents(database_path)
    return PreparedRestoreArchive(
        manifest=manifest,
        database_path=database_path,
        uploads_path=uploads_path,
        documents=documents,
        document_row_count=len(documents),
    )
