import asyncio
import json
import os
import re

from fastapi import APIRouter, Depends, File, HTTPException, Request, UploadFile
from fastapi.responses import StreamingResponse
from limiter import limiter
from sqlalchemy import delete as sa_delete
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from config import settings
from models.database import async_session, get_db
from models.orm import DocStatus, Document
from rag.pipeline import _process_document, ingest_document

router = APIRouter(prefix="/api/documents", tags=["documents"])

ALLOWED_TYPES = {
    ".pdf": "application/pdf",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".txt": "text/plain",
    ".md": "text/markdown",
    ".csv": "text/csv",
    ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
}

MAX_FILE_SIZE = 50 * 1024 * 1024  # 50MB


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

    # Validate size
    content = await file.read()
    if len(content) > MAX_FILE_SIZE:
        raise HTTPException(400, f"File too large: {len(content)} bytes (max {MAX_FILE_SIZE})")

    try:
        doc_id = await ingest_document(
            filename=file.filename or "unknown",
            file_content=content,
            file_type=ext,
            background=True,
        )
    except ValueError as e:
        raise HTTPException(409, str(e)) from e

    doc = (await db.execute(select(Document).where(Document.id == doc_id))).scalar_one()
    return {
        "id": doc.id,
        "filename": doc.filename,
        "file_size": doc.file_size,
        "file_type": doc.file_type,
        "status": doc.status.value,
        "chunk_count": doc.chunk_count,
        "created_at": doc.created_at.isoformat(),
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
    from storage.files import delete_file

    from textdb.bm25_search import BM25Search
    from vectordb.factory import create_vectordb

    result = await db.execute(select(Document))
    docs = result.scalars().all()

    if not docs:
        return {"status": "cleared", "count": 0}

    vectordb = await create_vectordb()
    fts = BM25Search()
    upload_dir = settings.upload_dir

    for doc in docs:
        await vectordb.delete_by_document(doc.id)
        await fts.delete_by_document(doc.id)

        if os.path.isdir(upload_dir):
            stem = doc.filename.rsplit(".", 1)[0] if "." in doc.filename else doc.filename
            if stem:
                for f in os.listdir(upload_dir):
                    if f == doc.filename or (f.startswith(stem + "_") and f.endswith(doc.file_type)):
                        delete_file(os.path.join(upload_dir, f))

    count = len(docs)
    await db.execute(sa_delete(Document))
    await db.commit()

    return {"status": "cleared", "count": count}

@router.delete("/{doc_id}")
async def delete_document(doc_id: str, db: AsyncSession = Depends(get_db)):
    from storage.files import delete_file

    from textdb.bm25_search import BM25Search
    from vectordb.factory import create_vectordb

    result = await db.execute(select(Document).where(Document.id == doc_id))
    doc = result.scalar_one_or_none()
    if not doc:
        raise HTTPException(404, "Document not found")

    # Clean up in order
    vectordb = await create_vectordb()
    fts = BM25Search()

    await vectordb.delete_by_document(doc_id)
    await fts.delete_by_document(doc_id)

    # Try to delete file
    upload_dir = settings.upload_dir
    if os.path.isdir(upload_dir):
        stem = doc.filename.rsplit(".", 1)[0] if "." in doc.filename else doc.filename
        if stem:
            for f in os.listdir(upload_dir):
                if f == doc.filename or (f.startswith(stem + "_") and f.endswith(doc.file_type)):
                    delete_file(os.path.join(upload_dir, f))

    await db.delete(doc)
    await db.commit()
    return {"status": "deleted", "id": doc_id}


@router.get("/{doc_id}/chunks")
async def get_document_chunks(doc_id: str, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Document).where(Document.id == doc_id))
    doc = result.scalar_one_or_none()
    if not doc:
        raise HTTPException(404, "Document not found")

    if not re.fullmatch(r"[a-zA-Z0-9\-_]+", doc_id):
        raise HTTPException(400, "Invalid document ID")

    async with async_session() as session:
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


@router.post("/{doc_id}/reprocess")
async def reprocess_document(doc_id: str, db: AsyncSession = Depends(get_db)):
    # Atomic status check-and-claim to prevent concurrent reprocess
    result = await db.execute(
        update(Document)
        .where(Document.id == doc_id)
        .where(Document.status.in_((DocStatus.failed, DocStatus.ready)))
        .values(status=DocStatus.uploaded, error_message=None)
        .returning(Document.filename, Document.file_type)
    )
    row = result.one_or_none()
    if not row:
        # Either doc doesn't exist or status is not failed/ready
        check = await db.execute(select(Document.id).where(Document.id == doc_id))
        if check.scalar_one_or_none() is None:
            raise HTTPException(404, "Document not found")
        raise HTTPException(400, "Only failed or ready documents can be reprocessed")
    await db.commit()

    filename, file_type = row

    # Find file path
    upload_dir = settings.upload_dir
    if not os.path.isdir(upload_dir):
        raise HTTPException(404, "Upload directory not found")

    stem = filename.rsplit(".", 1)[0] if "." in filename else filename
    file_path = None
    for f in os.listdir(upload_dir):
        if f == filename:
            file_path = os.path.join(upload_dir, f)
            break
        if stem and f.startswith(stem + "_") and f.endswith(file_type):
            file_path = os.path.join(upload_dir, f)
            break

    if not file_path:
        raise HTTPException(404, "Original file not found")

    try:
        await _process_document(doc_id, file_path, file_type)
    except Exception as e:
        import traceback
        traceback.print_exc()
        await db.execute(
            update(Document)
            .where(Document.id == doc_id)
            .values(status=DocStatus.failed, error_message=str(e))
        )
        await db.commit()
        raise HTTPException(500, f"Reprocessing failed: {e}") from e

    return {"status": "reprocessed", "id": doc_id}


@router.get("/{doc_id}/progress")
async def document_progress(doc_id: str):
    from rag.progress import progress

    async def event_stream():
        q = await progress.subscribe(doc_id)
        try:
            # 先推送当前状态
            async with async_session() as session:
                result = await session.execute(
                    select(Document).where(Document.id == doc_id)
                )
                doc = result.scalar_one_or_none()
                if doc:
                    yield f"data: {{\"status\": \"{doc.status.value}\"}}\n\n"
                else:
                    yield "data: {\"status\": \"not_found\"}\n\n"
                    return

            # 推送进度事件
            while True:
                try:
                    event = await asyncio.wait_for(q.get(), timeout=30)
                    yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
                    if event.get("status") in ("ready", "failed"):
                        break
                except TimeoutError:
                    yield "data: {\"status\": \"timeout\"}\n\n"
                    break
        finally:
            progress.unsubscribe(doc_id, q)

    return StreamingResponse(
        event_stream(),
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


