"""Backup and restore endpoints.

Backup: dump SQLite + upload files into a tar.gz download.
Restore: upload tar.gz, replace SQLite, re-ingest documents to rebuild vector indexes.
"""

import io
import logging
import shutil
import tarfile
import tempfile
from pathlib import Path

from fastapi import APIRouter, HTTPException, UploadFile
from fastapi.responses import FileResponse
from sqlalchemy import select

from config import settings
from models.database import async_session
from models.orm import DocStatus, Document

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/backup", tags=["backup"])


def _sqlite_db_path() -> Path:
    """Extract SQLite file path from the async SQLAlchemy URL."""
    url = str(settings.database_url)
    if url.startswith("sqlite+aiosqlite:///"):
        db_path = url[len("sqlite+aiosqlite:///"):]
        p = Path(db_path)
        # Relative path is resolved from the backend directory (config parent)
        if not p.is_absolute():
            p = Path(__file__).resolve().parent.parent / p
        return p.resolve()
    raise RuntimeError(f"Unsupported database URL: {url}")


@router.get("")
async def create_backup():
    """Create a full backup archive (SQLite + uploaded files) and return as download."""
    try:
        db_path = _sqlite_db_path()
        if not db_path.exists():
            raise HTTPException(500, f"数据库文件不存在: {db_path}")

        upload_dir = Path(settings.upload_dir)
        if not upload_dir.is_absolute():
            upload_dir = Path(__file__).resolve().parent.parent / upload_dir

        # 1. VACUUM INTO a temp copy (safe online backup for SQLite)
        backup_dir = Path(tempfile.mkdtemp(prefix="rag_backup_"))
        backup_db = backup_dir / "rag_agent.db"

        # Use aiosqlite or sqlite3 directly for VACUUM INTO
        import sqlite3
        conn = sqlite3.connect(str(db_path))
        conn.execute(f"VACUUM INTO '{backup_db.as_posix()}'")
        conn.close()

        # 2. Create tar.gz with DB + uploads
        tar_path = backup_dir / "backup.tar.gz"
        with tarfile.open(tar_path, "w:gz") as tar:
            tar.add(backup_db, arcname="rag_agent.db")
            if upload_dir.exists():
                tar.add(upload_dir, arcname="uploads")

        logger.info("backup created db=%s size=%d", db_path, tar_path.stat().st_size)
        return FileResponse(
            tar_path,
            media_type="application/gzip",
            filename="rag_agent_backup.tar.gz",
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error("backup failed", exc_info=True)
        raise HTTPException(500, f"备份失败: {e}") from e


@router.post("/restore")
async def restore_backup(file: UploadFile):
    """Restore from a backup archive. Replaces SQLite and re-indexes all documents."""
    if not file.filename or not file.filename.endswith((".tar.gz", ".tgz")):
        raise HTTPException(400, "请上传 .tar.gz 格式的备份文件")

    restore_dir = Path(tempfile.mkdtemp(prefix="rag_restore_"))
    try:
        # 1. Extract archive
        content = await file.read()
        with tarfile.open(fileobj=io.BytesIO(content), mode="r:gz") as tar:
            tar.extractall(restore_dir)

        # 2. Verify required files
        db_file = restore_dir / "rag_agent.db"
        uploads_dir = restore_dir / "uploads"
        if not db_file.exists():
            raise HTTPException(400, "备份文件不完整：缺少 rag_agent.db")

        # 3. Stop accepting connections briefly, then replace
        db_path = _sqlite_db_path()
        db_path.parent.mkdir(parents=True, exist_ok=True)

        # Close any open connections by disposing the engine
        from models.database import engine
        await engine.dispose()

        shutil.copy2(db_file, db_path)

        # 4. Restore uploaded files
        target_upload = Path(settings.upload_dir)
        if not target_upload.is_absolute():
            target_upload = Path(__file__).resolve().parent.parent / target_upload
        if uploads_dir.exists():
            if target_upload.exists():
                shutil.rmtree(target_upload)
            shutil.copytree(uploads_dir, target_upload)

        # 5. Re-ingest all ready documents to rebuild vector indexes.
        #    Delete existing doc records first — ingest_document rejects hash dupes.
        from rag.pipeline import ingest_document
        from textdb.bm25_search import BM25Search
        from vectordb.factory import create_vectordb

        async with async_session() as session:
            result = await session.execute(
                select(Document).where(Document.status == DocStatus.ready)
            )
            docs = result.scalars().all()

            # Purge vector/keyword data for these docs
            if docs:
                vectordb = await create_vectordb()
                fts = BM25Search()
                for doc in docs:
                    await vectordb.delete_by_document(doc.id)
                    await fts.delete_by_document(doc.id)
                    await session.delete(doc)
                await session.commit()

        if docs:
            reingested = 0
            for doc in docs:
                try:
                    file_path = Path(settings.upload_dir) / doc.filename
                    if not file_path.is_absolute():
                        file_path = Path(__file__).resolve().parent.parent / file_path
                    if file_path.exists():
                        content_bytes = file_path.read_bytes()
                        await ingest_document(
                            filename=doc.filename,
                            file_content=content_bytes,
                            file_type=doc.file_type or ".txt",
                            background=False,
                        )
                        reingested += 1
                except Exception as e:
                    logger.warning("re-ingest failed for doc %s: %s", doc.id, str(e)[:200])

            logger.info("restore complete: %d/%d docs re-ingested", reingested, len(docs))
            return {
                "status": "ok",
                "documents_total": len(docs),
                "documents_reingested": reingested,
            }
        else:
            logger.info("restore complete: no documents to re-ingest")
            return {"status": "ok", "documents_total": 0, "documents_reingested": 0}

    except HTTPException:
        raise
    except Exception as e:
        logger.error("restore failed", exc_info=True)
        raise HTTPException(500, f"恢复失败: {e}") from e
    finally:
        shutil.rmtree(restore_dir, ignore_errors=True)
