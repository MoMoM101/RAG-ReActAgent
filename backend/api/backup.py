"""Backup and restore endpoints.

Backup: dump SQLite + upload files into a tar.gz download with manifest.
Restore: staged restore with maintenance lock, verification, atomic switch, and rollback.
"""

import contextlib
import hashlib
import json
import logging
import shutil
import sqlite3
import tarfile
import tempfile
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

from fastapi import APIRouter, HTTPException, UploadFile
from fastapi.responses import FileResponse
from maintenance import MaintenancePhase, get_maintenance_state
from starlette.background import BackgroundTask

from config import settings
from utils.safe_archive import (
    ArchiveLimits,
    ArchiveSecurityError,
    read_upload_chunked,
    safe_extract_tar,
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/backup", tags=["backup"])

BACKUP_FORMAT_VERSION = 1


# ── Restore data types ────────────────────────────────────────────────


@dataclass
class RestoreTarget:
    """Explicit target resources for a restore operation (staging or live)."""

    database_path: Path
    upload_dir: Path
    qdrant_collection: str
    staging: bool = False


@dataclass
class RestoreDocument:
    """Document metadata from backup, preserving original IDs."""

    id: str
    filename: str
    file_type: str
    file_hash: str
    raw_text: str = ""
    chunk_count: int = 0

    @classmethod
    def from_row(cls, row: sqlite3.Row) -> "RestoreDocument":
        return cls(
            id=row["id"],
            filename=row["filename"],
            file_type=row["file_type"] or ".txt",
            file_hash=row["file_hash"] or "",
            raw_text=row["raw_text"] or "",
            chunk_count=row["chunk_count"] or 0,
        )


# ── Helpers ───────────────────────────────────────────────────────────


def _resolve_path(relative: str) -> Path:
    p = Path(relative)
    if not p.is_absolute():
        p = (Path(__file__).resolve().parent.parent / p).resolve()
    return p


def _sqlite_db_path() -> Path:
    url = str(settings.database_url)
    if url.startswith("sqlite+aiosqlite:///"):
        return _resolve_path(url[len("sqlite+aiosqlite:///"):])
    raise RuntimeError(f"Unsupported database URL: {url}")


def _backup_limits() -> ArchiveLimits:
    return ArchiveLimits(
        upload_bytes=settings.backup_max_upload_mb * 1024 * 1024,
        extracted_bytes=settings.backup_max_extracted_mb * 1024 * 1024,
        member_count=settings.backup_max_members,
    )


def _build_manifest(db_path: Path, upload_dir: Path) -> dict[str, Any]:
    """Build a manifest describing the backup contents."""
    manifest: dict[str, Any] = {
        "format_version": BACKUP_FORMAT_VERSION,
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "collection_name": settings.qdrant_active_collection or settings.qdrant_collection,
        "files": cast(dict[str, Any], {}),
    }

    def _hash_file(p: Path) -> str:
        return hashlib.sha256(p.read_bytes()).hexdigest()

    manifest["files"]["rag_agent.db"] = {
        "sha256": _hash_file(db_path),
        "size": db_path.stat().st_size,
    }

    if upload_dir.exists():
        for f in sorted(upload_dir.rglob("*")):
            if f.is_file():
                manifest["files"][str(f.relative_to(upload_dir.parent))] = {
                    "sha256": _hash_file(f),
                    "size": f.stat().st_size,
                }

    return manifest


def _verify_manifest(restore_dir: Path, manifest: dict) -> None:
    """Verify every file in the manifest matches its recorded hash."""
    for rel_path, info in manifest.get("files", {}).items():
        fp = restore_dir / rel_path
        if not fp.exists() or not fp.is_file():
            raise HTTPException(400, f"备份文件缺少: {rel_path}")
        actual = hashlib.sha256(fp.read_bytes()).hexdigest()
        if actual != info.get("sha256"):
            raise HTTPException(400, f"备份文件校验失败: {rel_path}")


@router.get("")
async def create_backup():
    """Create a full backup archive (SQLite + uploaded files) and return as download."""
    try:
        db_path = _sqlite_db_path()
        if not db_path.exists():
            raise HTTPException(500, f"数据库文件不存在: {db_path}")

        upload_dir = _resolve_path(settings.upload_dir)

        backup_dir = Path(tempfile.mkdtemp(prefix="rag_backup_"))
        backup_db = backup_dir / "rag_agent.db"

        conn = sqlite3.connect(str(db_path))
        conn.execute(f"VACUUM INTO '{backup_db.as_posix()}'")
        conn.close()

        manifest = _build_manifest(backup_db, upload_dir)
        manifest_path = backup_dir / "manifest.json"
        manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")

        tar_path = backup_dir / "backup.tar.gz"
        with tarfile.open(tar_path, "w:gz") as tar:
            tar.add(manifest_path, arcname="manifest.json")
            tar.add(backup_db, arcname="rag_agent.db")
            if upload_dir.exists():
                tar.add(upload_dir, arcname="uploads")

        logger.info("backup created db=%s size=%d", db_path, tar_path.stat().st_size)
        return FileResponse(
            tar_path,
            media_type="application/gzip",
            filename="rag_agent_backup.tar.gz",
            background=BackgroundTask(shutil.rmtree, backup_dir, ignore_errors=True),
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error("backup failed", exc_info=True)
        raise HTTPException(500, f"备份失败: {e}") from e


async def _build_restore_qdrant(
    docs: list[RestoreDocument],
    staging_dir: Path,
    embedding_dim: int,
    collection_name: str,
) -> str:
    """Build Qdrant indexes for restore documents in a named collection.

    Uses original document IDs and stable chunk IDs. Returns the collection name.
    """
    import uuid as _uuid

    from embedding.factory import create_embedding
    from rag.loaders import load_document
    from rag.splitter import split_text
    from vectordb.qdrant import QdrantVectorDB

    embedding = create_embedding()
    vdb = QdrantVectorDB(collection_name=collection_name)
    await vdb.ensure_collection(embedding_dim)

    reingested = 0
    for doc in docs:
        doc_path = staging_dir / "uploads" / doc.filename
        if not doc_path.exists():
            logger.warning("restore doc file missing: %s", doc.filename)
            continue

        try:
            raw_text = load_document(str(doc_path), doc.file_type)
        except Exception as e:
            logger.warning("restore doc parse failed %s: %s", doc.filename, str(e)[:200])
            continue

        chunks = split_text(raw_text, settings.chunk_size, settings.chunk_overlap)
        if not chunks:
            logger.warning("restore doc empty after split: %s", doc.filename)
            continue

        texts = [c.text for c in chunks]
        try:
            vectors = await embedding.embed(texts)
        except Exception as e:
            logger.warning("restore embed failed %s: %s", doc.filename, str(e)[:200])
            continue

        doc_key = _document_key_stable(doc.id, doc.filename)
        points = []
        for chunk, vector in zip(chunks, vectors, strict=False):
            chunk_id = str(_uuid.uuid5(_uuid.NAMESPACE_DNS, f"{doc.id}:{chunk.chunk_index}"))
            points.append({
                "id": chunk_id,
                "vector": vector,
                "payload": {
                    "document_id": doc.id,
                    "document_key": doc_key,
                    "chunk_index": chunk.chunk_index,
                    "text": chunk.text,
                    "section_key": chunk.section_key,
                },
            })

        await vdb.upsert(points)
        reingested += 1
        logger.info("restore re-indexed doc %s (%d chunks)", doc.filename, len(chunks))

    logger.info("restore qdrant built: %d/%d docs, collection=%s", reingested, len(docs), collection_name)
    return collection_name


async def _cross_consistency_check(
    db_path: Path,
    qdrant_collection: str,
    uploads_dir: Path,
) -> dict:
    """Verify consistency across SQLite, Qdrant, and uploads."""
    from vectordb.qdrant import QdrantVectorDB

    result: dict = {"passed": True, "issues": []}

    # Read ready docs from SQLite
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    try:
        rows = list(conn.execute("SELECT id, filename, file_hash, chunk_count FROM documents WHERE status = 'ready'"))
    finally:
        conn.close()

    if not rows:
        result["passed"] = True
        result["note"] = "no ready documents to check"
        return result

    vdb = QdrantVectorDB(collection_name=qdrant_collection)
    for row in rows:
        doc_id = row["id"]
        filename = row["filename"]
        expected_chunks = row["chunk_count"] or 0

        # Check upload file exists
        upload_path = uploads_dir / filename
        if not upload_path.exists():
            result["issues"].append(f"upload missing: {filename}")
            result["passed"] = False
            continue

        # Check Qdrant point count
        try:
            import asyncio as _asyncio

            from qdrant_client.models import FieldCondition, Filter, MatchValue
            count_result = await _asyncio.to_thread(
                vdb.client.count,
                collection_name=qdrant_collection,
                count_filter=Filter(must=[FieldCondition(
                    key="document_id", match=MatchValue(value=doc_id)
                )]),
            )
            actual_count = count_result.count
            if expected_chunks > 0 and actual_count != expected_chunks:
                result["issues"].append(
                    f"{filename}: expected {expected_chunks} Qdrant points, got {actual_count}"
                )
                result["passed"] = False
        except Exception as e:
            result["issues"].append(f"{filename}: Qdrant check failed: {e}")
            result["passed"] = False

    return result


async def _save_active_collection_pointer(collection_name: str) -> None:
    """Persist the active Qdrant collection name to active_collections.json."""
    ptr_file = Path(settings.qdrant_path) / "active_collections.json"
    data = {}
    if ptr_file.exists():
        with contextlib.suppress(Exception):
            data = json.loads(ptr_file.read_text())
    data["rag_chunks"] = collection_name
    tmp = ptr_file.with_suffix(".tmp")
    tmp.write_text(json.dumps(data), encoding="utf-8")
    tmp.replace(ptr_file)


def _document_key_stable(doc_id: str, filename: str) -> str:
    """Derive a stable document_key from filename."""
    import re
    base = filename.rsplit(".", 1)[0] if "." in filename else filename
    return re.sub(r"[^a-zA-Z0-9-]", "-", base).strip("-").lower() or doc_id[:8]


# ── Restore endpoint ──────────────────────────────────────────────────


@router.post("/restore")
async def restore_backup(file: UploadFile):
    """Staged restore with atomic switch across SQLite, uploads, and Qdrant.

    Key invariants:
    - Live data is never modified before the atomic switch.
    - Original document IDs from the backup are preserved.
    - Qdrant is rebuilt in a temporary collection, then pointer-switched.
    - Rollback reverts the Qdrant pointer as well as SQLite/uploads.
    """
    if not file.filename or not file.filename.endswith((".tar.gz", ".tgz")):
        raise HTTPException(400, "请上传 .tar.gz 格式的备份文件")

    mstate = get_maintenance_state()
    if not await mstate.acquire():
        raise HTTPException(503, "系统正在执行维护操作，请稍后重试", headers={"Retry-After": "30"})

    limits = _backup_limits()
    restore_dir = Path(tempfile.mkdtemp(prefix="rag_restore_"))
    staging_dir = Path(tempfile.mkdtemp(prefix="rag_restore_staging_"))
    rollback_dir = Path(tempfile.mkdtemp(prefix="rag_restore_rollback_"))
    # Snapshot current Qdrant collection for rollback
    old_collection = settings.qdrant_active_collection or settings.qdrant_collection
    temp_collection = f"rag_chunks_restore_{uuid.uuid4().hex[:12]}"

    try:
        # ---- Phase 1: Verify ----
        mstate.update(MaintenancePhase.VERIFYING, 5, "读取并验证备份文件")
        try:
            content = read_upload_chunked(file.file, limits.upload_bytes)
        except ArchiveSecurityError as e:
            raise HTTPException(413, str(e)) from e

        import io as _io
        with tarfile.open(fileobj=_io.BytesIO(content), mode="r:gz") as tar:
            try:
                safe_extract_tar(tar, restore_dir, limits)
            except ArchiveSecurityError as e:
                raise HTTPException(400, f"备份文件安全校验失败: {e}") from e

        manifest_path = restore_dir / "manifest.json"
        manifest = None
        if manifest_path.exists():
            try:
                manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            except json.JSONDecodeError as e:
                raise HTTPException(400, "备份文件 manifest.json 格式无效") from e
            _verify_manifest(restore_dir, manifest)

        db_file = restore_dir / "rag_agent.db"
        uploads_dir = restore_dir / "uploads"
        if not db_file.exists():
            raise HTTPException(400, "备份文件不完整：缺少 rag_agent.db")

        conn = sqlite3.connect(str(db_file))
        try:
            cur = conn.execute("PRAGMA integrity_check")
            result = cur.fetchone()
            if result and result[0] != "ok":
                raise HTTPException(400, f"备份数据库完整性检查失败: {result[0]}")
        finally:
            conn.close()

        # Read restore documents from backup DB (preserving original IDs)
        conn = sqlite3.connect(str(db_file))
        conn.row_factory = sqlite3.Row
        try:
            staging_rows = list(conn.execute(
                "SELECT id, filename, file_type, file_hash, raw_text, chunk_count "
                "FROM documents WHERE status = 'ready'"
            ).fetchall())
        finally:
            conn.close()
        restore_docs = [RestoreDocument.from_row(r) for r in staging_rows]

        # ---- Phase 2: Stage current data for rollback ----
        mstate.update(MaintenancePhase.STAGING, 15, "保护当前数据用于回滚")
        db_path = _sqlite_db_path()
        target_upload = _resolve_path(settings.upload_dir)

        if db_path.exists():
            shutil.copy2(db_path, rollback_dir / "rag_agent.db")
        if target_upload.exists():
            shutil.copytree(target_upload, rollback_dir / "uploads", dirs_exist_ok=True)

        # Copy backup data to staging directory
        staging_db = staging_dir / "rag_agent.db"
        staging_uploads = staging_dir / "uploads"
        shutil.copy2(db_file, staging_db)
        if uploads_dir.exists():
            shutil.copytree(uploads_dir, staging_uploads, dirs_exist_ok=True)

        # Verify staging SQLite
        conn = sqlite3.connect(str(staging_db))
        try:
            cur = conn.execute("PRAGMA integrity_check")
            result = cur.fetchone()
            if result and result[0] != "ok":
                raise HTTPException(500, f"暂存数据库完整性检查失败: {result[0]}")
        finally:
            conn.close()

        # ---- Phase 3: Build Qdrant indexes in temp collection ----
        mstate.update(MaintenancePhase.BUILDING, 35, "在临时集合中重建向量索引")
        from embedding.factory import create_embedding
        embedding = create_embedding()
        test_vec = await embedding.embed_query("dimension test")
        embedding_dim = len(test_vec)

        await _build_restore_qdrant(
            restore_docs, staging_dir, embedding_dim, temp_collection
        )

        # ---- Phase 4: Cross-consistency check ----
        mstate.update(MaintenancePhase.VERIFYING, 60, "执行交叉一致性检查")
        check = await _cross_consistency_check(
            staging_db, temp_collection, staging_uploads
        )
        if not check["passed"]:
            issues = "; ".join(check["issues"][:5])
            raise RuntimeError(f"交叉一致性检查失败: {issues}")

        # ---- Phase 5: Atomic switch ----
        mstate.update(MaintenancePhase.SWITCHING, 75, "原子切换到新数据")

        from models.database import engine
        await engine.dispose()

        # Atomic SQLite replacement
        db_path.parent.mkdir(parents=True, exist_ok=True)
        tmp_db = db_path.with_suffix(".restore_tmp")
        shutil.copy2(staging_db, tmp_db)
        tmp_db.replace(db_path)

        # Atomic Qdrant pointer switch
        settings.qdrant_active_collection = temp_collection
        await _save_active_collection_pointer(temp_collection)

        # Uploads switch via os.replace
        if staging_uploads.exists():
            if target_upload.exists():
                old_uploads = target_upload.with_name(target_upload.name + ".old")
                if old_uploads.exists():
                    shutil.rmtree(old_uploads)
                target_upload.rename(old_uploads)
            shutil.copytree(staging_uploads, str(target_upload))
            if target_upload.with_name(target_upload.name + ".old").exists():
                shutil.rmtree(target_upload.with_name(target_upload.name + ".old"), ignore_errors=True)

        # ---- Phase 6: Health check ----
        mstate.update(MaintenancePhase.CLEANING, 90, "执行切换后健康检查")
        conn = sqlite3.connect(str(db_path))
        try:
            cur = conn.execute("PRAGMA integrity_check")
            result = cur.fetchone()
            if result and result[0] != "ok":
                raise RuntimeError(f"切换后数据库健康检查失败: {result[0]}")
        finally:
            conn.close()

        # Re-init database engine for new SQLite
        from models.database import init_db
        await init_db()

        mstate.update(MaintenancePhase.IDLE, 100, "恢复完成")
        logger.info(
            "restore complete: %d docs restored, collection=%s",
            len(restore_docs), temp_collection,
        )
        return {
            "status": "ok",
            "documents_total": len(staging_rows),
            "documents_restored": len(restore_docs),
            "collection": temp_collection,
        }

    except HTTPException:
        await _do_rollback(rollback_dir, old_collection, db_path, target_upload, mstate)
        raise

    except Exception as e:
        logger.error("restore failed", exc_info=True)
        await _do_rollback(rollback_dir, old_collection, db_path, target_upload, mstate, str(e))
        raise HTTPException(500, f"恢复失败: {e}") from e

    finally:
        mstate.release()
        shutil.rmtree(restore_dir, ignore_errors=True)
        shutil.rmtree(staging_dir, ignore_errors=True)
        shutil.rmtree(rollback_dir, ignore_errors=True)


async def _do_rollback(
    rollback_dir: Path,
    old_collection: str,
    db_path: Path,
    target_upload: Path,
    mstate,
    error_msg: str | None = None,
) -> None:
    """Rollback SQLite, uploads, and Qdrant pointer to pre-restore state."""
    if error_msg:
        mstate.set_error(error_msg)
    mstate.update(MaintenancePhase.ROLLING_BACK, 0, "回滚到恢复前状态")

    from models.database import engine
    await engine.dispose()

    rollback_db = rollback_dir / "rag_agent.db"
    if rollback_db.exists():
        tmp_db = db_path.with_suffix(".rollback_tmp")
        shutil.copy2(rollback_db, tmp_db)
        tmp_db.replace(db_path)

    rollback_uploads = rollback_dir / "uploads"
    if rollback_uploads.exists():
        if target_upload.exists():
            shutil.rmtree(target_upload)
        shutil.copytree(rollback_uploads, str(target_upload))

    # Restore Qdrant pointer
    settings.qdrant_active_collection = old_collection
    await _save_active_collection_pointer(old_collection)

    from models.database import init_db
    await init_db()

    mstate.update(MaintenancePhase.IDLE, 0, "回滚完成，系统正常")
    logger.info("rollback complete, collection restored to %s", old_collection)


@router.get("/maintenance-status")
async def maintenance_status():
    """Return current maintenance operation status."""
    return get_maintenance_state().snapshot()
