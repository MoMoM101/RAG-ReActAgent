import uuid
import hashlib
import time
import logging
from sqlalchemy import select
from models.database import async_session
from models.orm import Document, DocStatus
from rag.loaders import load_document
from rag.splitter import split_text
from embedding.factory import create_embedding
from vectordb.factory import create_vectordb
from textdb.sqlite_fts import SQLiteFTS5
from storage.files import save_upload
from config import settings

logger = logging.getLogger(__name__)

_ingestion_semaphore = None


def _get_semaphore() -> "asyncio.Semaphore":
    import asyncio
    global _ingestion_semaphore
    if _ingestion_semaphore is None:
        _ingestion_semaphore = asyncio.Semaphore(settings.ingestion_max_concurrency)
    return _ingestion_semaphore


async def ingest_document(
    filename: str,
    file_content: bytes,
    file_type: str,
    background: bool = False,
) -> str:
    """Run full ingestion. Returns document_id. Raises ValueError on duplicate.

    If background=True, processing runs asynchronously and the function returns immediately.
    """
    import asyncio

    doc_id = str(uuid.uuid4())
    file_hash = hashlib.sha256(file_content).hexdigest()
    file_size = len(file_content)

    # 1. Check for duplicates
    async with async_session() as session:
        result = await session.execute(
            select(Document).where(Document.file_hash == file_hash)
        )
        if result.scalar_one_or_none():
            raise ValueError(f"File '{filename}' already exists (hash matched)")

    # 2. Save file
    file_path = save_upload(file_content, filename)

    # 3. Create document record
    async with async_session() as session:
        doc = Document(
            id=doc_id,
            filename=filename,
            file_hash=file_hash,
            file_size=file_size,
            file_type=file_type,
            status=DocStatus.uploaded,
        )
        session.add(doc)
        await session.commit()

    # 4. Run ingestion steps (sync or background)
    if background:
        async def _bg_process():
            sem = _get_semaphore()
            async with sem:
                started = time.time()
                logger.info("ingestion started doc_id=%s filename=%s", doc_id, filename)
                from rag.progress import progress
                for attempt in range(2):
                    try:
                        await _process_document(doc_id, file_path, file_type)
                        elapsed = (time.time() - started) * 1000
                        logger.info("ingestion complete doc_id=%s elapsed_ms=%d", doc_id, int(elapsed))
                        return
                    except Exception as e:
                        if attempt == 0:
                            logger.warning("ingestion retry doc_id=%s error=%s", doc_id, str(e)[:200])
                            await asyncio.sleep(10)
                        else:
                            async with async_session() as session:
                                result = await session.execute(select(Document).where(Document.id == doc_id))
                                doc = result.scalar_one()
                                doc.status = DocStatus.failed
                                doc.error_message = f"[重试1次后失败] {e}"
                                await session.commit()
                            logger.error("ingestion failed doc_id=%s error=%s", doc_id, str(e)[:200])
                            progress.publish(doc_id, {"status": "failed", "error": str(e)[:200]})
        asyncio.create_task(_bg_process())
        return doc_id

    try:
        await _process_document(doc_id, file_path, file_type)
    except Exception as e:
        async with async_session() as session:
            result = await session.execute(select(Document).where(Document.id == doc_id))
            doc = result.scalar_one()
            doc.status = DocStatus.failed
            doc.error_message = str(e)
            await session.commit()
        raise

    return doc_id


async def _process_document(doc_id: str, file_path: str, file_type: str):
    from rag.progress import progress
    async with async_session() as session:
        result = await session.execute(select(Document).where(Document.id == doc_id))
        doc = result.scalar_one()

        # Parse
        doc.status = DocStatus.parsing
        raw_text = load_document(file_path, file_type)
        doc.raw_text = raw_text
        await session.commit()
        progress.publish(doc_id, {"status": "parsing", "message": "正在解析文档..."})

        # Chunk
        doc.status = DocStatus.chunking
        await session.commit()
        progress.publish(doc_id, {"status": "chunking", "message": "正在切分文本..."})
        chunks = split_text(raw_text, settings.chunk_size, settings.chunk_overlap)
        logger.info("chunking done doc_id=%s chunks=%d", doc_id, len(chunks))

        if not chunks:
            progress.publish(doc_id, {"status": "failed", "error": "No text extracted from document"})
            raise ValueError("No text extracted from document")

        # Embed (with adaptive chunk_size fallback)
        doc.status = DocStatus.embedding
        await session.commit()
        embedding = create_embedding()
        texts = [c.text for c in chunks]

        actual_chunk_size = settings.chunk_size
        for attempt in range(10):
            try:
                progress.publish(doc_id, {
                    "status": "embedding",
                    "message": f"正在向量化 ({len(chunks)} 个分块)...",
                    "chunk_count": len(chunks),
                })
                logger.info("embedding started doc_id=%s chunk_count=%d", doc_id, len(chunks))
                t_emb = time.time()
                vectors = await embedding.embed(texts)
                emb_elapsed = int((time.time() - t_emb) * 1000)
                logger.info("embedding done doc_id=%s elapsed_ms=%d vectors=%d", doc_id, emb_elapsed, len(vectors))
                break
            except Exception as e:
                err_msg = str(e)[:300].lower()
                is_length_error = (
                    "too long" in err_msg
                    or "too many token" in err_msg
                    or "maximum context" in err_msg
                    or "context length" in err_msg
                    or "token limit" in err_msg
                    or ("reduce" in err_msg and "length" in err_msg)
                )
                if attempt < 9 and is_length_error:
                    new_size = max(actual_chunk_size * 2 // 3, 8)
                    logger.warning(
                        "embedding chunk_size too large, retrying doc_id=%s old=%d new=%d",
                        doc_id, actual_chunk_size, new_size,
                    )
                    actual_chunk_size = new_size
                    chunks = split_text(raw_text, actual_chunk_size, settings.chunk_overlap)
                    texts = [c.text for c in chunks]
                    progress.publish(doc_id, {
                        "status": "embedding",
                        "message": f"自适应调整切分大小至 {actual_chunk_size}，重试中...",
                        "chunk_count": len(chunks),
                    })
                    doc.chunk_count = len(chunks)
                    doc.chunk_size = actual_chunk_size
                else:
                    raise
        else:
            raise RuntimeError(f"Embedding failed after 10 attempts (final chunk_size={actual_chunk_size})")

        # Index: dual write (clean old data first for idempotent retry)
        doc.status = DocStatus.indexing
        await session.commit()
        progress.publish(doc_id, {"status": "indexing", "message": "正在写入索引..."})
        t_idx = time.time()

        vectordb = await create_vectordb()
        fts = SQLiteFTS5()
        await vectordb.delete_by_document(doc_id)
        await fts.delete_by_document(doc_id)

        points = []
        for chunk, vector in zip(chunks, vectors):
            chunk_id = str(uuid.uuid5(uuid.NAMESPACE_DNS, f"{doc_id}:{chunk.chunk_index}"))
            points.append({
                "id": chunk_id,
                "vector": vector,
                "payload": {
                    "document_id": doc_id,
                    "chunk_index": chunk.chunk_index,
                    "text": chunk.text,
                },
            })

        # Qdrant 先写，FTS5 后写：Qdrant 失败时 FTS5 干净，不会残留垃圾
        await vectordb.upsert(points)
        for chunk in chunks:
            chunk_id = str(uuid.uuid5(uuid.NAMESPACE_DNS, f"{doc_id}:{chunk.chunk_index}"))
            await fts.insert(chunk_id, doc_id, chunk.text)
        idx_elapsed = int((time.time() - t_idx) * 1000)
        logger.info("indexing done doc_id=%s elapsed_ms=%d", doc_id, idx_elapsed)

        # Done
        doc.status = DocStatus.ready
        doc.chunk_count = len(chunks)
        doc.embedding_model = settings.embedding_model
        doc.embedding_dim = settings.embedding_dim
        doc.chunk_size = actual_chunk_size
        await session.commit()

        progress.publish(doc_id, {
            "status": "ready",
            "chunk_count": len(chunks),
            "message": "入库完成",
        })
