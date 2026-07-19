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

from audit import record_audit
from config import settings
from utils.safe_archive import (
    ArchiveLimits,
    ArchiveSecurityError,
    read_upload_chunked,
    safe_extract_tar,
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/backup", tags=["backup"])

BACKUP_FORMAT_VERSION = 2


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


def _git_commit() -> str:
    """Return current git commit hash (short) or 'unknown'."""
    import subprocess
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            capture_output=True, text=True, timeout=5,
        )
        return result.stdout.strip() if result.returncode == 0 else "unknown"
    except Exception:
        return "unknown"


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


def _get_alembic_revision(db_path: Path) -> str:
    """Read Alembic revision from the database."""
    conn = sqlite3.connect(str(db_path))
    try:
        row = conn.execute("SELECT version_num FROM alembic_version").fetchone()
        return row[0] if row else "unknown"
    except sqlite3.OperationalError:
        return "legacy"
    finally:
        conn.close()


def _get_head_info() -> tuple[str, "ScriptDirectory"]:
    """Return (head_revision, ScriptDirectory) for revision classification.

    ScriptDirectory is returned alongside head to avoid constructing it twice
    (once for verification, once for migration).
    """
    from alembic.config import Config as AlcCfg
    from alembic.script import ScriptDirectory
    backend_dir = Path(__file__).resolve().parent.parent
    cfg = AlcCfg(str(backend_dir / "alembic.ini"))
    script_dir = ScriptDirectory.from_config(cfg)
    heads = script_dir.get_heads()
    return (heads[0] if heads else "unknown", script_dir)


def _classify_staged_revision(
    staged_revision: str | None,
    head: str,
    script_dir: "ScriptDirectory",
) -> str:
    """Classify staged revision relative to current head.

    Returns one of: 'legacy', 'current', 'old', 'unknown', 'future'.
    - legacy: no alembic_version table/row in staged DB
    - current: staged revision == head
    - old: staged revision is an ancestor of head (needs migration)
    - unknown: revision string not found in local migration history
    - future: revision exists in history but is not reachable from head (descendant or parallel branch)
    """
    if staged_revision is None:
        return "legacy"
    if staged_revision == head:
        return "current"

    # Check if the revision exists in the local migration history at all
    try:
        script_dir.get_revision(staged_revision)
    except Exception:
        return "unknown"

    # Check if staged_revision is an ancestor of head by walking from head down
    try:
        list(script_dir.iterate_revisions(head, staged_revision))
        return "old"
    except Exception:
        return "future"


async def _migrate_staged_db(db_path: Path, head_revision: str) -> None:
    """Run alembic upgrade head on a staged SQLite database file.

    Sets sqlalchemy.url on the Alembic config so env.py targets the staged
    file instead of the live database (env.py reads this option first, then
    falls back to settings.database_url). Verifies the revision reached head
    after migration.

    Raises RuntimeError on migration failure or post-migration revision mismatch.
    """
    from alembic.config import Config as AlcCfg
    from alembic import command as alc_cmd

    backend_dir = Path(__file__).resolve().parent.parent
    cfg = AlcCfg(str(backend_dir / "alembic.ini"))
    cfg.set_main_option("sqlalchemy.url", f"sqlite+aiosqlite:///{db_path.as_posix()}")

    try:
        import asyncio as _aio
        await _aio.to_thread(alc_cmd.upgrade, cfg, "head")
    except Exception as e:
        raise RuntimeError(f"staged migration failed: {e}") from e

    # Verify migration reached head
    new_revision = _get_alembic_revision(db_path)
    if new_revision != head_revision:
        raise RuntimeError(
            f"staged migration verification failed: expected revision {head_revision}, "
            f"got {new_revision}"
        )


def _move_or_copy(src: Path, dst: Path) -> None:
    """Move directory via rename (atomic on same fs), fall back to copytree."""
    src = Path(src)
    dst = Path(dst)
    try:
        src.rename(dst)
    except OSError:
        shutil.copytree(str(src), str(dst))
        shutil.rmtree(src, ignore_errors=True)


def _verify_uploads_candidate(candidate_dir: Path, manifest: dict | None) -> None:
    """Verify candidate uploads match the manifest (file count, size, SHA-256)."""
    if not candidate_dir.exists():
        return
    actual_files = sorted(
        [p.relative_to(candidate_dir).as_posix() for p in candidate_dir.rglob("*") if p.is_file()]
    )
    # Manifest verification — only check uploads/ entries
    if manifest and "files" in manifest:
        uploads_prefix = "uploads/"
        manifest_upload_files = {
            k[len(uploads_prefix):]: v
            for k, v in manifest["files"].items()
            if k.startswith(uploads_prefix)
        }
        if not manifest_upload_files:
            return  # no upload files in manifest, nothing to verify
        manifest_set = set(manifest_upload_files.keys())
        actual_set = set(actual_files)
        missing = manifest_set - actual_set
        extra = actual_set - manifest_set
        if missing:
            raise RuntimeError(f"候选上传目录缺少清单中的文件: {', '.join(sorted(missing))}")
        if extra:
            raise RuntimeError(f"候选上传目录包含清单外的多余文件: {', '.join(sorted(extra))}")
        # Verify SHA-256 for each upload file
        for fname in manifest_set:
            fpath = candidate_dir / fname
            if not fpath.is_file():
                raise RuntimeError(f"候选上传文件不存在: {fname}")
            expected_hash = manifest_upload_files[fname].get("sha256", "")
            if expected_hash:
                actual_hash = hashlib.sha256(fpath.read_bytes()).hexdigest()
                if actual_hash != expected_hash:
                    raise RuntimeError(
                        f"候选上传文件哈希不匹配: {fname} "
                        f"(expected={expected_hash[:12]}..., actual={actual_hash[:12]}...)"
                    )


def _cleanup_restore_artifacts(upload_dir: Path) -> list[str]:
    """Clean up leftover candidate/previous directories from interrupted restores.

    Called at startup. Returns list of cleaned directory names.
    """
    cleaned = []
    parent = upload_dir.resolve()
    for pattern in ("uploads.candidate.*", "uploads.previous.*"):
        for p in sorted(parent.parent.glob(pattern)):
            try:
                shutil.rmtree(p, ignore_errors=False)
                cleaned.append(p.name)
                logger.warning("startup cleanup: removed leftover %s", p.name)
            except OSError as e:
                logger.warning("startup cleanup: could not remove %s: %s", p.name, e)
    return cleaned


# ── Qdrant collection lifecycle ────────────────────────────────────────

# Prefixes for classifying collections
_RESTORE_TEMP_PREFIX = "rag_chunks_restore_"
_ACTIVE_VARIANTS = {"rag_chunks"}  # base names that can be active


async def _delete_restore_temp_collection(collection_name: str) -> bool:
    """Delete a restore temp collection. Returns True on success, False on failure."""
    try:
        from vectordb.qdrant import QdrantVectorDB
        vdb = QdrantVectorDB(collection_name=collection_name)
        vdb.client.delete_collection(collection_name)
        logger.info("deleted restore temp collection: %s", collection_name)
        return True
    except Exception as e:
        logger.warning("failed to delete restore temp collection %s: %s", collection_name, e)
        return False


async def _cleanup_orphan_qdrant_collections(active: str) -> dict:
    """Audit and clean orphan Qdrant collections at startup.

    Does NOT blindly delete unmatched collections — only removes expired
    restore temp collections. Returns cleanup metrics.
    """
    from vectordb.qdrant import QdrantVectorDB

    metrics: dict = {"audited": 0, "deleted": 0, "kept": 0, "errors": 0}
    try:
        vdb = QdrantVectorDB()
        import asyncio
        all_collections = await asyncio.to_thread(vdb.client.get_collections)
    except Exception as e:
        logger.warning("startup collection audit failed: %s", e)
        return metrics

    active_base = settings.qdrant_collection  # e.g. "rag_chunks"

    for c in all_collections.collections:
        metrics["audited"] += 1
        name = c.name
        # Skip active and base collections
        if name in (active, active_base):
            metrics["kept"] += 1
            continue
        # Clean stale restore temp collections
        if name.startswith(_RESTORE_TEMP_PREFIX):
            # Qdrant doesn't expose creation timestamp, so we delete restore temps
            # older than the cutoff if we can determine age, otherwise keep
            metrics["kept"] += 1  # conservative: keep unless we can verify staleness
        else:
            metrics["kept"] += 1

    logger.info(
        "startup collection audit: %d total, %d deleted, %d kept, %d errors",
        metrics["audited"], metrics["deleted"], metrics["kept"], metrics["errors"],
    )
    return metrics


async def _retain_collections_after_restore(
    new_active: str,
    old_active: str,
    temp_collection: str,
) -> dict:
    """Manage collection lifecycle after a successful restore.

    - Keep new_active (just became the live collection)
    - Keep old_active as 'previous' for emergency rollback
    - Delete temp_collection (was renamed to active, should already be gone)
    - Delete any other expired restore temp collections

    Returns cleanup metrics.
    """
    import asyncio

    from vectordb.qdrant import QdrantVectorDB

    metrics: dict = {"deleted": 0, "errors": 0, "kept": 0}
    try:
        vdb = QdrantVectorDB()
        all_collections = await asyncio.to_thread(vdb.client.get_collections)
    except Exception as e:
        logger.warning("post-restore collection cleanup failed: %s", e)
        return metrics

    active_base = settings.qdrant_collection
    to_keep = {new_active, old_active, active_base}

    for c in all_collections.collections:
        name = c.name
        if name in to_keep:
            metrics["kept"] += 1
            continue
        # Delete orphan restore temp collections
        if name.startswith(_RESTORE_TEMP_PREFIX):
            if await _delete_restore_temp_collection(name):
                metrics["deleted"] += 1
            else:
                metrics["errors"] += 1
        else:
            metrics["kept"] += 1

    logger.info(
        "post-restore collection lifecycle: deleted=%d, kept=%d, errors=%d",
        metrics["deleted"], metrics["kept"], metrics["errors"],
    )
    return metrics


def _build_manifest(db_path: Path, upload_dir: Path) -> dict[str, Any]:
    """Build a manifest describing the backup contents with full compatibility info."""
    manifest: dict[str, Any] = {
        "format_version": BACKUP_FORMAT_VERSION,
        "schema_version": BACKUP_FORMAT_VERSION,
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "git_commit": _git_commit(),
        "collection_name": settings.qdrant_active_collection or settings.qdrant_collection,
        "db_schema_revision": _get_alembic_revision(db_path),
        "database_sha256": hashlib.sha256(db_path.read_bytes()).hexdigest(),
        # Compatibility info for restore-time validation
        "embedding": {
            "provider": settings.embedding_provider,
            "model": settings.embedding_model,
            "dimension": settings.embedding_dim,
        },
        "chunking": {
            "size": settings.chunk_size,
            "overlap": settings.chunk_overlap,
        },
        "bm25": {
            "schema_version": 1,
            "tokenizer": "jieba",
        },
        "ocr": {
            "enabled": settings.ocr_enabled,
        },
        "rerank": {
            "enabled": settings.rerank_enabled,
            "model": settings.rerank_model,
        },
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
                rel = str(f.relative_to(upload_dir.parent))
                manifest["files"][rel] = {
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


def _validate_manifest_compatibility(manifest: dict) -> None:
    """Check restore compatibility: embedding dimension, chunk config, BM25 schema.

    Raises HTTPException with a clear message if the backup is incompatible.
    """
    issues = []

    # Embedding dimension must match
    backup_emb = manifest.get("embedding", {})
    if backup_emb:
        backup_dim = backup_emb.get("dimension", 0)
        if backup_dim and backup_dim != settings.embedding_dim:
            issues.append(
                f"Embedding 维度不兼容: 备份={backup_dim}, 当前={settings.embedding_dim}。"
                f"请使用相同 embedding 模型或手动执行全量重建。"
            )

    # Chunk config: warn if different (not a hard block, but logged)
    backup_chunk = manifest.get("chunking", {})
    if backup_chunk:
        if backup_chunk.get("size", 0) != settings.chunk_size:
            logger.warning(
                "manifest chunk_size mismatch: backup=%d, current=%d",
                backup_chunk.get("size"), settings.chunk_size,
            )
        if backup_chunk.get("overlap", 0) != settings.chunk_overlap:
            logger.warning(
                "manifest chunk_overlap mismatch: backup=%d, current=%d",
                backup_chunk.get("overlap"), settings.chunk_overlap,
            )

    # BM25 schema version
    backup_bm25 = manifest.get("bm25", {})
    if backup_bm25 and backup_bm25.get("schema_version", 1) != 1:
        issues.append(
            f"BM25 schema 版本不兼容: 备份={backup_bm25.get('schema_version')}, 当前=1"
        )

    # Missing uploads: flag if manifest lists upload files
    manifest_files = manifest.get("files", {})
    upload_files = [k for k in manifest_files if k.startswith("uploads/")]
    if not upload_files and manifest_files:
        logger.warning("manifest has no upload files; restore will skip uploads")

    if issues:
        raise HTTPException(400, "备份兼容性检查失败:\n" + "\n".join(f"  - {i}" for i in issues))


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
        await record_audit("backup_download")
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
    """Verify consistency across SQLite, Qdrant, BM25, and uploads.

    Checks per-document alignment of chunk counts and document IDs across all
    four storage layers. Total equality is not sufficient — ID-level mismatches
    are flagged as failures.
    """
    from vectordb.qdrant import QdrantVectorDB

    result: dict = {"passed": True, "issues": [], "details": {}}

    # Read ready docs from SQLite
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    try:
        rows = list(conn.execute(
            "SELECT id, filename, file_hash, chunk_count "
            "FROM documents WHERE status = 'ready'"
        ))
    finally:
        conn.close()

    if not rows:
        result["passed"] = True
        result["note"] = "no ready documents to check"
        return result

    doc_ids = [row["id"] for row in rows]

    # ── BM25 consistency: check per-document chunk counts ──
    bm25_counts = await _bm25_doc_chunk_counts(doc_ids, db_path=db_path)

    # ── Qdrant consistency ──
    vdb = QdrantVectorDB(collection_name=qdrant_collection)

    for row in rows:
        doc_id = row["id"]
        filename = row["filename"]
        expected_chunks = row["chunk_count"] or 0
        details: dict = {"document_id": doc_id, "filename": filename}

        # 1. Upload file existence + hash
        upload_path = uploads_dir / filename
        if not upload_path.exists():
            result["issues"].append(f"upload missing: {filename}")
            result["passed"] = False
            details["upload"] = "missing"
        else:
            actual_hash = hashlib.sha256(upload_path.read_bytes()).hexdigest()
            expected_hash = row["file_hash"] or ""
            details["upload"] = "present"
            # Only verify hash if stored value looks like a real SHA-256 (64 hex chars)
            if expected_hash and len(expected_hash) == 64 and actual_hash != expected_hash:
                result["issues"].append(
                    f"{filename}: hash mismatch (expected={expected_hash[:12]}..., "
                    f"actual={actual_hash[:12]}...)"
                )
                result["passed"] = False
                details["hash"] = "mismatch"
            else:
                details["hash"] = "ok"

        # 2. Qdrant point count
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
            qdrant_count = count_result.count
            details["qdrant_chunks"] = qdrant_count
            if expected_chunks > 0 and qdrant_count != expected_chunks:
                result["issues"].append(
                    f"{filename}: Qdrant count mismatch (expected={expected_chunks}, actual={qdrant_count})"
                )
                result["passed"] = False
        except Exception as e:
            result["issues"].append(f"{filename}: Qdrant check failed: {e}")
            result["passed"] = False
            details["qdrant_chunks"] = "error"

        # 3. BM25 chunk count (skip if BM25 isn't populated yet, e.g. during restore staging)
        bm25_count = bm25_counts.get(doc_id)
        if bm25_count is not None:
            details["bm25_chunks"] = bm25_count
            if expected_chunks > 0 and bm25_count != expected_chunks:
                result["issues"].append(
                    f"{filename}: BM25 count mismatch (expected={expected_chunks}, actual={bm25_count})"
                )
                result["passed"] = False
        else:
            details["bm25_chunks"] = "skipped"

        details["expected_chunks"] = expected_chunks
        result["details"][doc_id] = details

    # ── Aggregate summary ──
    result["summary"] = {
        "total_docs": len(rows),
        "sqlite_ready": len(rows),
        "qdrant_collection": qdrant_collection,
        "bm25_docs_indexed": len([
            d for d in bm25_counts
            if bm25_counts.get(d) is not None and (bm25_counts.get(d) or 0) > 0
        ]),
        "uploads_present": len([r for r in rows if (uploads_dir / r["filename"]).exists()]),
    }

    return result


async def _bm25_doc_chunk_counts(
    doc_ids: list[str], *, db_path: Path | None = None,
) -> dict[str, int | None]:
    """Query BM25 index for chunk counts per document_id.

    During restore, ``db_path`` is the staged database. Reading the live async
    engine here would validate unrelated online data instead of the candidate
    being switched in. Returns None when an older backup has no BM25 tables.
    """
    if db_path is not None:
        try:
            conn = sqlite3.connect(str(db_path))
            try:
                table = conn.execute(
                    "SELECT 1 FROM sqlite_master WHERE type='table' AND name='bm25_docs'"
                ).fetchone()
                if table is None:
                    return {doc_id: None for doc_id in doc_ids}
                total = conn.execute("SELECT COUNT(*) FROM bm25_docs").fetchone()
                if not total or total[0] == 0:
                    return {doc_id: None for doc_id in doc_ids}
                return {
                    doc_id: int(conn.execute(
                        "SELECT COUNT(*) FROM bm25_docs WHERE document_id = ?",
                        (doc_id,),
                    ).fetchone()[0])
                    for doc_id in doc_ids
                }
            finally:
                conn.close()
        except Exception as e:
            logger.warning("staged BM25 consistency check skipped: %s", e)
            return {doc_id: None for doc_id in doc_ids}

    try:
        from textdb.bm25_search import BM25Search
        bm25 = BM25Search()

        # Check if BM25 tables actually exist before querying
        try:
            total = await bm25._query(f"SELECT COUNT(*) FROM {bm25._docs}")
            if not total or total[0][0] == 0:
                return {doc_id: None for doc_id in doc_ids}
        except Exception:
            return {doc_id: None for doc_id in doc_ids}

        counts: dict[str, int | None] = {}
        for doc_id in doc_ids:
            rows = await bm25._query_param(
                f"SELECT COUNT(*) FROM {bm25._docs} WHERE document_id = :did",
                {"did": doc_id},
            )
            counts[doc_id] = rows[0][0] if rows else 0
        return counts
    except Exception as e:
        logger.warning("BM25 consistency check skipped: %s", e)
        return {doc_id: None for doc_id in doc_ids}


async def _save_active_collection_pointer(collection_name: str) -> None:
    """Persist the active Qdrant collection name to active_collections.json."""
    ptr_file = Path(settings.qdrant_path) / "active_collections.json"
    ptr_file.parent.mkdir(parents=True, exist_ok=True)
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
    # Resolve live paths early so rollback can always reference them
    db_path = _sqlite_db_path()
    target_upload = _resolve_path(settings.upload_dir)
    restore_id = uuid.uuid4().hex[:12]

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
            _validate_manifest_compatibility(manifest)

        db_file = restore_dir / "rag_agent.db"
        uploads_dir = restore_dir / "uploads"
        if not db_file.exists():
            raise HTTPException(400, "备份文件不完整：缺少 rag_agent.db")

        try:
            conn = sqlite3.connect(str(db_file))
        except sqlite3.Error as e:
            raise HTTPException(400, f"备份数据库无法打开: {e}") from e
        try:
            cur = conn.execute("PRAGMA integrity_check")
            result = cur.fetchone()
            if result and result[0] != "ok":
                raise HTTPException(400, f"备份数据库完整性检查失败: {result[0]}")
        except sqlite3.Error as e:
            raise HTTPException(400, f"备份数据库完整性检查失败: {e}") from e
        finally:
            conn.close()

        # ── Version gate: staged DB as authority ──
        staged_revision_raw = _get_alembic_revision(db_file)
        staged_revision = None if staged_revision_raw in ("legacy", "unknown") else staged_revision_raw
        head_revision, script_dir = _get_head_info()

        # Cross-validate manifest db_schema_revision against staged DB
        if manifest is not None:
            manifest_revision = manifest.get("db_schema_revision")
            if manifest_revision is not None:
                if staged_revision is None:
                    raise HTTPException(
                        400,
                        f"备份 manifest 声明版本 {manifest_revision}，"
                        f"但备份数据库未包含 Alembic 版本信息。"
                        "该备份可能损坏或来自旧版本应用，请通过离线流程采纳。",
                    )
                if manifest_revision != staged_revision:
                    raise HTTPException(
                        400,
                        f"备份 manifest 版本 ({manifest_revision}) 与数据库实际版本 "
                        f"({staged_revision}) 不一致，备份文件可能损坏。",
                    )

        # Classify and act on staged revision
        classification = _classify_staged_revision(staged_revision, head_revision, script_dir)

        if classification == "legacy":
            raise HTTPException(
                400,
                "备份数据库不包含版本信息（legacy 格式）。"
                "legacy 备份不支持直接恢复。请通过离线采纳流程："
                "1) 提取备份中的数据库文件，2) 核对表结构指纹，"
                "3) 使用 alembic stamp 标记版本后重新打包。",
            )
        elif classification in ("unknown", "future"):
            detail = (
                f"备份数据库版本 '{staged_revision}' 不被当前应用识别。"
                "请升级应用到包含此版本的新版本。"
            )
            raise HTTPException(400, detail)
        elif classification == "old":
            logger.info(
                "staged DB revision %s < head %s, running staged migration",
                staged_revision, head_revision,
            )
            try:
                await _migrate_staged_db(db_file, head_revision)
            except RuntimeError as e:
                raise HTTPException(400, f"备份数据库迁移失败: {e}") from e
            logger.info("staged migration complete, revision now at %s", head_revision)

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

        # Uploads atomic switch: candidate → rename → live
        # All renames happen within the same parent directory (same filesystem),
        # making each rename an atomic directory entry operation.
        if staging_uploads.exists() and any(staging_uploads.iterdir()):
            candidate_uploads = target_upload.parent / f"uploads.candidate.{restore_id}"
            previous_uploads = target_upload.parent / f"uploads.previous.{restore_id}"

            # 1. Move staging to candidate (rename if same fs, copytree fallback)
            _move_or_copy(staging_uploads, candidate_uploads)

            # 2. Verify candidate integrity before switching
            _verify_uploads_candidate(candidate_uploads, manifest)

            # 3. Atomic switch: live → previous, candidate → live
            if target_upload.exists():
                target_upload.rename(previous_uploads)
            try:
                candidate_uploads.rename(target_upload)
            except OSError:
                # candidate → live failed: restore previous → live
                if previous_uploads.exists():
                    previous_uploads.rename(target_upload)
                raise RuntimeError(
                    "上传目录原子切换失败：candidate rename 失败，已恢复旧目录"
                ) from None

            # 4. Clean up previous after successful switch
            if previous_uploads.exists():
                shutil.rmtree(previous_uploads, ignore_errors=True)

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

        # Post-restore collection lifecycle: retain old_active for emergency rollback
        # and clean up stale restore temp collections.
        cleanup_metrics = await _retain_collections_after_restore(
            new_active=temp_collection,
            old_active=old_collection,
            temp_collection=temp_collection,
        )
        documents_restored = len(restore_docs)

        await record_audit("backup_restore",
                           detail=f"documents_restored={documents_restored}")
        return {
            "status": "ok",
            "documents_total": len(staging_rows),
            "documents_restored": len(restore_docs),
            "collection": temp_collection,
            "collection_cleanup": cleanup_metrics,
        }

    except HTTPException:
        await _do_rollback(rollback_dir, old_collection, db_path, target_upload, mstate,
                           temp_collection=temp_collection)
        raise

    except Exception as e:
        logger.error("restore failed", exc_info=True)
        await _do_rollback(rollback_dir, old_collection, db_path, target_upload, mstate,
                           error_msg=str(e), temp_collection=temp_collection)
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
    temp_collection: str = "",
) -> None:
    """Rollback SQLite, uploads, and Qdrant pointer to pre-restore state.

    Deletes the failed restore's temp Qdrant collection to prevent leaks.
    """
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
        # Clean up any leftover candidate/previous from the failed switch
        parent = target_upload.parent
        for pattern in ("uploads.candidate.*", "uploads.previous.*"):
            for p in sorted(parent.glob(pattern)):
                shutil.rmtree(p, ignore_errors=True)
        shutil.copytree(rollback_uploads, str(target_upload))

    # Restore Qdrant pointer
    settings.qdrant_active_collection = old_collection
    await _save_active_collection_pointer(old_collection)

    # Clean up the failed restore's temp Qdrant collection
    if temp_collection:
        await _delete_restore_temp_collection(temp_collection)

    from models.database import init_db
    await init_db()

    mstate.update(MaintenancePhase.IDLE, 0, "回滚完成，系统正常")
    logger.info("rollback complete, collection restored to %s", old_collection)


@router.get("/maintenance-status")
async def maintenance_status():
    """Return current maintenance operation status."""
    return get_maintenance_state().snapshot()
