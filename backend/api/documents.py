import asyncio
import hashlib
import json
import logging
import os
import re

from fastapi import APIRouter, Depends, File, HTTPException, Request, UploadFile
from fastapi.responses import StreamingResponse
from sqlalchemy import delete as sa_delete
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from audit import audit_from_request, record_audit
from config import DOCUMENT_UPLOAD_HARD_LIMIT_MB, settings
from limiter import limiter
from models.database import get_db, session_scope
from models.orm import DocStatus, Document
from rag.answer_cache import bump_collection_version
from rag.loaders import OCRModelNotReadyError
from rag.pipeline import _mark_waiting_for_ocr, _process_document, ingest_document_from_staged
from storage import get_storage, materialize, stage_path
from storage.base import StagedObject
from storage.files import delete_file, find_upload

router = APIRouter(prefix="/api/documents", tags=["documents"])
logger = logging.getLogger(__name__)

ALLOWED_TYPES = {
    ".pdf": "application/pdf",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".txt": "text/plain",
    ".md": "text/markdown",
    ".csv": "text/csv",
    ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
}

UPLOAD_CHUNK_SIZE = 1024 * 1024  # 1MB
SSE_HEARTBEAT_SECONDS = 15
TERMINAL_DOCUMENT_STATUSES = {"ready", "failed", "waiting_for_ocr", "not_found"}


async def _delete_document_source(doc: Document) -> None:
    """Delete a committed object, with a hash-safe legacy-file fallback."""
    await _delete_document_source_by_attrs(
        doc.storage_key, doc.filename, doc.file_type, doc.file_hash,
    )


async def _delete_document_source_by_attrs(
    storage_key: str | None, filename: str, file_type: str, file_hash: str,
) -> None:
    """Delete file source using individual attributes (safe after doc expire)."""
    storage = get_storage()
    if storage_key and await storage.exists(storage_key):
        await storage.delete(storage_key)
        return
    legacy_path = find_upload(
        filename, file_type,
        expected_sha256=file_hash,
        root_dir=settings.upload_dir,
    )
    if legacy_path:
        delete_file(legacy_path)


async def _ensure_document_storage(doc: Document) -> str | None:
    """Return a usable storage key, importing a legacy flat upload if needed."""
    storage = get_storage()
    if doc.storage_key and await storage.exists(doc.storage_key):
        return doc.storage_key
    legacy_path = find_upload(
        doc.filename,
        doc.file_type,
        expected_sha256=doc.file_hash,
        root_dir=settings.upload_dir,
    )
    if legacy_path is None:
        return None
    staged = await stage_path(storage, legacy_path, doc.filename)
    expected_hash = doc.file_hash if len(doc.file_hash) == 64 else None
    stored = await storage.commit(staged, expected_sha256=expected_hash)
    doc.storage_key = stored.storage_key
    return stored.storage_key


def _max_file_size_bytes() -> int:
    return settings.document_max_upload_mb * 1024 * 1024


def _document_payload(doc: Document) -> dict:
    return {
        "id": doc.id,
        "filename": doc.filename,
        "file_size": doc.file_size,
        "file_type": doc.file_type,
        "status": doc.status.value,
        "chunk_count": doc.chunk_count,
        "created_at": doc.created_at.isoformat(),
    }


async def _stream_upload_to_staging(
    file: UploadFile,
) -> tuple[StagedObject, str, int]:
    """Stream an upload through the configured storage backend."""
    storage = get_storage()
    staged = await storage.create_staging(file.filename or "unknown")
    digest = hashlib.sha256()
    total = 0
    max_file_size = _max_file_size_bytes()
    try:
        while chunk := await file.read(UPLOAD_CHUNK_SIZE):
            total += len(chunk)
            if total > max_file_size:
                raise HTTPException(
                    413,
                    "File too large: exceeds "
                    f"{settings.document_max_upload_mb} MB",
                )
            digest.update(chunk)
            await storage.append(staged, chunk)
        if total == 0:
            raise HTTPException(400, "File is empty")
        return staged, digest.hexdigest(), total
    except BaseException:
        await storage.abort(staged)
        raise
    finally:
        await file.close()


@router.post("/upload")
@limiter.limit("10/minute")
async def upload_document(
    request: Request,
    file: UploadFile = File(...),
    db: AsyncSession = Depends(get_db),
):
    # Validate extension
    ext = os.path.splitext(file.filename or "")[1].lower()
    if ext not in ALLOWED_TYPES:
        raise HTTPException(400, f"Unsupported file type: {ext}")

    staged, file_hash, file_size = await _stream_upload_to_staging(file)
    try:
        doc_id = await ingest_document_from_staged(
            filename=file.filename or "unknown",
            staged=staged,
            file_hash=file_hash,
            file_size=file_size,
            file_type=ext,
            background=True,
        )
    except ValueError as e:
        raise HTTPException(409, str(e)) from e
    finally:
        # No-op after commit; removes staging data on early failures.
        await get_storage().abort(staged)

    doc = (await db.execute(select(Document).where(Document.id == doc_id))).scalar_one()
    await audit_from_request(request, "document_upload",
                             object_type="document", object_id=doc_id,
                             detail=f"filename={doc.filename}")
    return _document_payload(doc)


@router.post("/upload-batch")
@limiter.limit("10/minute")
async def upload_document_batch(
    request: Request,
    files: list[UploadFile] = File(...),
    db: AsyncSession = Depends(get_db),
):
    """Upload multiple files with per-file failure isolation."""
    if not files:
        raise HTTPException(400, "No files provided")
    if len(files) > settings.document_batch_max_files:
        raise HTTPException(
            400,
            f"Too many files: maximum {settings.document_batch_max_files} per batch",
        )
    known_total = sum(file.size or 0 for file in files)
    total_limit = settings.document_batch_max_total_mb * 1024 * 1024
    if known_total > total_limit:
        raise HTTPException(
            413,
            "Batch too large: exceeds "
            f"{settings.document_batch_max_total_mb} MB total limit",
        )

    items: list[dict] = []
    streamed_total = 0
    for file in files:
        filename = file.filename or "unknown"
        ext = os.path.splitext(filename)[1].lower()
        if ext not in ALLOWED_TYPES:
            await file.close()
            items.append({
                "filename": filename,
                "success": False,
                "status_code": 400,
                "error": f"Unsupported file type: {ext}",
            })
            continue

        staged: StagedObject | None = None
        try:
            staged, file_hash, file_size = await _stream_upload_to_staging(file)
            streamed_total += file_size
            if streamed_total > total_limit:
                items.append({
                    "filename": filename,
                    "success": False,
                    "status_code": 413,
                    "error": "Batch total size limit exceeded",
                })
                continue
            doc_id = await ingest_document_from_staged(
                filename=filename,
                staged=staged,
                file_hash=file_hash,
                file_size=file_size,
                file_type=ext,
                background=True,
            )
            doc = (
                await db.execute(select(Document).where(Document.id == doc_id))
            ).scalar_one()
            items.append({
                "filename": filename,
                "success": True,
                "document": _document_payload(doc),
            })
        except HTTPException as exc:
            items.append({
                "filename": filename,
                "success": False,
                "status_code": exc.status_code,
                "error": str(exc.detail),
            })
        except ValueError as exc:
            items.append({
                "filename": filename,
                "success": False,
                "status_code": 409,
                "error": str(exc),
            })
        except Exception:
            logger.exception("Batch upload failed for file=%s", filename)
            items.append({
                "filename": filename,
                "success": False,
                "status_code": 500,
                "error": "Internal processing error",
            })
        finally:
            if staged is not None:
                await get_storage().abort(staged)

    succeeded = sum(bool(item["success"]) for item in items)
    failed = sum(not item["success"] for item in items)
    await audit_from_request(request, "document_upload_batch",
                             detail=f"succeeded={succeeded}, failed={failed}")
    return {
        "items": items,
        "total": len(items),
        "succeeded": succeeded,
        "failed": failed,
    }


@router.get("/upload-config")
async def upload_config():
    return {
        "max_upload_mb": settings.document_max_upload_mb,
        "hard_limit_mb": DOCUMENT_UPLOAD_HARD_LIMIT_MB,
        "batch_max_files": settings.document_batch_max_files,
        "batch_max_total_mb": settings.document_batch_max_total_mb,
        "allowed_extensions": sorted(ALLOWED_TYPES),
    }


@router.get("")
async def list_documents(db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        select(Document).order_by(Document.created_at.desc())
    )
    docs = result.scalars().all()
    return [
        {
            "id": d.id,
            "filename": d.filename,
            "file_size": d.file_size,
            "file_type": d.file_type,
            "status": d.status.value,
            "chunk_count": d.chunk_count,
            "error_message": d.error_message,
            "created_at": d.created_at.isoformat(),
        }
        for d in docs
    ]



@router.delete("/clear-all")
async def clear_all_documents(db: AsyncSession = Depends(get_db)):
    """删除全部文档（向量 + FTS + 文件 + DB 记录）。"""
    from textdb.bm25_search import BM25Search
    from vectordb.factory import create_vectordb

    result = await db.execute(select(Document))
    docs = result.scalars().all()

    if not docs:
        return {"status": "cleared", "count": 0}

    active = [
        doc for doc in docs
        if doc.status not in (DocStatus.ready, DocStatus.failed, DocStatus.waiting_for_ocr)
    ]
    if active:
        raise HTTPException(
            409,
            f"仍有 {len(active)} 个文档正在处理，请等待完成或失败后再清空",
        )

    # Extract attributes before deletion (expired after commit)
    doc_attrs = [
        (d.id, d.storage_key, d.filename, d.file_type, d.file_hash)
        for d in docs
    ]

    # Atomic SQLite deletion first
    count = len(docs)
    await db.execute(sa_delete(Document))
    await db.commit()

    # Best-effort external store cleanup
    vectordb = await create_vectordb()
    fts = BM25Search()
    for doc_id, storage_key, filename, file_type, file_hash in doc_attrs:
        try:
            await vectordb.delete_by_document(doc_id)
        except Exception as e:
            logger.warning("clear_all Qdrant cleanup failed doc_id=%s: %s", doc_id, e)
        try:
            await fts.delete_by_document(doc_id)
        except Exception as e:
            logger.warning("clear_all BM25 cleanup failed doc_id=%s: %s", doc_id, e)
        try:
            await _delete_document_source_by_attrs(storage_key, filename, file_type, file_hash)
        except Exception as e:
            logger.warning("clear_all file cleanup failed doc_id=%s: %s", doc_id, e)

    bump_collection_version()
    await record_audit("document_clear_all",
                       detail=f"count={count}")
    return {"status": "cleared", "count": count}

@router.delete("/{doc_id}")
async def delete_document(doc_id: str, db: AsyncSession = Depends(get_db)):
    from textdb.bm25_search import BM25Search
    from vectordb.factory import create_vectordb

    result = await db.execute(select(Document).where(Document.id == doc_id))
    doc = result.scalar_one_or_none()
    if not doc:
        raise HTTPException(404, "Document not found")
    if doc.status not in (DocStatus.ready, DocStatus.failed, DocStatus.waiting_for_ocr):
        raise HTTPException(409, "Document is still being processed")

    # Extract doc attributes before deletion (expired after commit)
    storage_key = doc.storage_key
    filename = doc.filename
    file_type = doc.file_type
    file_hash = doc.file_hash

    # Delete SQLite record first (atomic from user's perspective).
    # External store cleanup is best-effort — orphaned vectors are
    # harmless (filtered by _filter_committed_generation).
    await db.delete(doc)
    await db.commit()

    vectordb = await create_vectordb()
    fts = BM25Search()
    try:
        await vectordb.delete_by_document(doc_id)
    except Exception as e:
        logger.warning("delete_document Qdrant cleanup failed doc_id=%s: %s", doc_id, e)
    try:
        await fts.delete_by_document(doc_id)
    except Exception as e:
        logger.warning("delete_document BM25 cleanup failed doc_id=%s: %s", doc_id, e)
    try:
        await _delete_document_source_by_attrs(storage_key, filename, file_type, file_hash)
    except Exception as e:
        logger.warning("delete_document file cleanup failed doc_id=%s: %s", doc_id, e)

    bump_collection_version()
    await record_audit("document_delete",
                       object_type="document", object_id=doc_id)
    return {"status": "deleted", "id": doc_id}


@router.get("/{doc_id}/chunks")
async def get_document_chunks(doc_id: str, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Document).where(Document.id == doc_id))
    doc = result.scalar_one_or_none()
    if not doc:
        raise HTTPException(404, "Document not found")

    if not re.fullmatch(r"[a-zA-Z0-9\-_]+", doc_id):
        raise HTTPException(400, "Invalid document ID")

    async with session_scope() as session:
        conn = await session.connection()
        result = await conn.exec_driver_sql(
            "SELECT chunk_id, text FROM bm25_docs WHERE document_id = ? ORDER BY chunk_id",
            (doc_id,)
        )
        rows = result.fetchall()

    return {
        "document_id": doc_id,
        "filename": doc.filename,
        "chunks": [
            {"chunk_id": r[0], "text": r[1]}
            for r in rows
        ],
    }


@router.post("/{doc_id}/reprocess", status_code=202)
async def reprocess_document(doc_id: str, db: AsyncSession = Depends(get_db)):
    # Validate the document and source file before claiming it. The previous
    # order could leave a missing-file retry permanently stuck as "uploaded".
    doc = (
        await db.execute(select(Document).where(Document.id == doc_id))
    ).scalar_one_or_none()
    if doc is None:
        raise HTTPException(404, "Document not found")
    if doc.status not in (DocStatus.failed, DocStatus.ready, DocStatus.waiting_for_ocr):
        raise HTTPException(400, "Only failed, ready, or OCR-waiting documents can be reprocessed")

    file_type = doc.file_type
    storage_key = await _ensure_document_storage(doc)
    if storage_key is None:
        raise HTTPException(404, "Original file not found")

    # Atomic claim prevents duplicate clicks from scheduling duplicate work.
    claimed = await db.execute(
        update(Document)
        .where(Document.id == doc_id)
        .where(Document.status.in_((DocStatus.failed, DocStatus.ready, DocStatus.waiting_for_ocr)))
        .values(status=DocStatus.uploaded, error_message=None)
    )
    if claimed.rowcount != 1:
        await db.rollback()
        raise HTTPException(409, "Document is already being processed")
    await db.commit()
    bump_collection_version()

    from rag.progress import progress
    from worker.tasks import get_task_manager

    progress.publish(doc_id, {"status": "uploaded", "message": "已提交重新处理"})

    async def _background_reprocess():
        try:
            async with materialize(
                get_storage(), storage_key, suffix=file_type
            ) as file_path:
                await _process_document(doc_id, file_path, file_type)
        except OCRModelNotReadyError as exc:
            await _mark_waiting_for_ocr(doc_id, str(exc))
        except Exception as exc:
            error = str(exc)[:500]
            async with session_scope() as session:
                await session.execute(
                    update(Document)
                    .where(Document.id == doc_id)
                    .values(status=DocStatus.failed, error_message=error)
                )
                await session.commit()
            progress.publish(doc_id, {
                "status": "failed",
                "message": "重新处理失败",
                "error": error,
            })

    get_task_manager().create(
        _background_reprocess,
        f"reprocess_{doc_id[:8]}",
        metadata={"doc_id": doc_id},
    )

    return {"status": "queued", "id": doc_id}


async def _load_document_progress(doc_id: str) -> dict:
    """Load durable progress so SSE can recover from missed in-process events."""
    async with session_scope() as session:
        result = await session.execute(
            select(Document).where(Document.id == doc_id)
        )
        doc = result.scalar_one_or_none()
        if doc is None:
            return {"status": "not_found", "error": "Document not found"}

        event = {
            "status": doc.status.value,
            "chunk_count": doc.chunk_count,
        }
        if doc.error_message:
            event["error"] = doc.error_message
        return event


def _sse_data(event: dict) -> str:
    return f"data: {json.dumps(event, ensure_ascii=False)}\n\n"


async def _document_progress_events(doc_id: str):
    from rag.progress import progress

    q = await progress.subscribe(doc_id)
    try:
        yield "retry: 3000\n\n"

        current = await _load_document_progress(doc_id)
        last_status = current["status"]
        yield _sse_data(current)
        if last_status in TERMINAL_DOCUMENT_STATUSES:
            return

        while True:
            try:
                event = await asyncio.wait_for(
                    q.get(), timeout=SSE_HEARTBEAT_SECONDS
                )
            except TimeoutError:
                # The event bus is process-local. Querying durable state also
                # recovers completion after event loss, restart, or reconnect.
                current = await _load_document_progress(doc_id)
                status = current["status"]
                if status != last_status or status in TERMINAL_DOCUMENT_STATUSES:
                    last_status = status
                    yield _sse_data(current)
                else:
                    yield ": keepalive\n\n"
                if status in TERMINAL_DOCUMENT_STATUSES:
                    return
                continue

            status = event.get("status", last_status)
            last_status = status
            yield _sse_data(event)
            if status in TERMINAL_DOCUMENT_STATUSES:
                return
    finally:
        progress.unsubscribe(doc_id, q)


@router.get("/{doc_id}/progress")
async def document_progress(doc_id: str):

    return StreamingResponse(
        _document_progress_events(doc_id),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.get("/queue-stats")
async def queue_stats(db: AsyncSession = Depends(get_db)):
    """返回各状态文档数量，用于监控入库队列。"""
    result = await db.execute(
        select(Document.status, func.count(Document.id))
        .group_by(Document.status)
    )
    counts = {row[0].value: row[1] for row in result.all()}
    stuck = sum(
        counts.get(s.value, 0)
        for s in (DocStatus.parsing, DocStatus.chunking, DocStatus.embedding, DocStatus.indexing)
    )
    return {
        "total": sum(counts.values()),
        "by_status": counts,
        "stuck_in_progress": stuck,
    }
