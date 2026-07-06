import os
import asyncio
from fastapi import APIRouter, UploadFile, File, HTTPException, Depends, BackgroundTasks, Request
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func
from models.database import get_db, async_session
from models.orm import Document, DocStatus
from rag.pipeline import ingest_document, _process_document
from config import settings
from limiter import limiter
import json
from fastapi.responses import StreamingResponse

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
        raise HTTPException(409, str(e))

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


@router.delete("/{doc_id}")
async def delete_document(doc_id: str, db: AsyncSession = Depends(get_db)):
    from vectordb.factory import create_vectordb
    from textdb.sqlite_fts import SQLiteFTS5
    from storage.files import delete_file

    result = await db.execute(select(Document).where(Document.id == doc_id))
    doc = result.scalar_one_or_none()
    if not doc:
        raise HTTPException(404, "Document not found")

    # Clean up in order
    vectordb = await create_vectordb()
    fts = SQLiteFTS5()

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

    import re
    if not re.fullmatch(r"[a-zA-Z0-9\-_]+", doc_id):
        raise HTTPException(400, "Invalid document ID")

    async with async_session() as session:
        conn = await session.connection()
        result = await conn.exec_driver_sql(
            "SELECT chunk_id, content FROM chunks_fts WHERE document_id = ? ORDER BY chunk_id",
            (doc_id,)
        )
        rows = result.fetchall()

    return {
        "document_id": doc_id,
        "filename": doc.filename,
        "chunks": [
            {"chunk_id": r[0], "text": _desegment_cjk(r[1])}
            for r in rows
        ],
    }

import re
def _desegment_cjk(text: str) -> str:
    """Undo _segment_cjk spacing for display: ' 项 目 ' → '项目'"""
    # Collapse whitespace around CJK characters
    return re.sub(r'\s+(?=[一-鿿㐀-䶿豈-﫿])', '', text).strip()


@router.post("/{doc_id}/reprocess")
async def reprocess_document(doc_id: str, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Document).where(Document.id == doc_id))
    doc = result.scalar_one_or_none()
    if not doc:
        raise HTTPException(404, "Document not found")
    if doc.status not in (DocStatus.failed, DocStatus.ready):
        raise HTTPException(400, "Only failed or ready documents can be reprocessed")

    # Find file path
    upload_dir = settings.upload_dir
    if not os.path.isdir(upload_dir):
        raise HTTPException(404, "Upload directory not found")

    stem = doc.filename.rsplit(".", 1)[0] if "." in doc.filename else doc.filename
    file_path = None
    for f in os.listdir(upload_dir):
        if f == doc.filename:
            file_path = os.path.join(upload_dir, f)
            break
        if stem and f.startswith(stem + "_") and f.endswith(doc.file_type):
            file_path = os.path.join(upload_dir, f)
            break

    if not file_path:
        raise HTTPException(404, "Original file not found")

    # Reset status and retry (_process_document handles internal cleanup)
    doc.status = DocStatus.uploaded
    doc.error_message = None  # type: ignore[assignment]
    await db.commit()

    try:
        await _process_document(doc_id, file_path, doc.file_type)
    except Exception as e:
        import traceback
        traceback.print_exc()
        doc.status = DocStatus.failed
        doc.error_message = str(e)
        await db.commit()
        raise HTTPException(500, f"Reprocessing failed: {e}")

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
                    yield f"data: {{\"status\": \"not_found\"}}\n\n"
                    return

            # 推送进度事件
            while True:
                try:
                    event = await asyncio.wait_for(q.get(), timeout=30)
                    yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
                    if event.get("status") in ("ready", "failed"):
                        break
                except asyncio.TimeoutError:
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
