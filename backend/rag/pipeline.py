import asyncio
import hashlib
import hashlib as _hashlib
import logging
import time
import uuid

from sqlalchemy import select
from storage.files import delete_file, finalize_upload, find_upload, save_upload

from config import settings
from embedding.factory import create_embedding
from models.database import async_session
from models.orm import DocStatus, Document
from rag.loaders import load_document
from rag.splitter import split_text
from textdb.bm25_search import BM25Search
from vectordb.factory import create_vectordb

logger = logging.getLogger(__name__)

_ingestion_semaphore = None


def _classify_error(error: Exception) -> str:
    """Classify ingestion error: 'rate_limit' | 'transient' | 'permanent'."""
    msg = str(error).lower()
    if "429" in msg or "rate limit" in msg or "too many requests" in msg:
        return "rate_limit"
    if isinstance(error, (TimeoutError, ConnectionError)):
        return "transient"
    if any(kw in msg for kw in ("timeout", "connection", "reset", "refused")):
        return "transient"
    return "permanent"


def _retry_delay(attempt: int, error_type: str) -> float:
    """Calculate backoff delay with jitter for a given attempt and error type."""
    import random

    base = settings.ingestion_retry_base_sec
    if error_type == "rate_limit":
        base = base * 4  # 429 needs longer cooling
    delay = min(base * (2 ** attempt), settings.ingestion_retry_max_sec)
    jitter = delay * settings.ingestion_retry_jitter
    return delay + random.uniform(-jitter, jitter)


def _hash_chunk_ids(chunk_ids: set[str]) -> str:
    """Deterministic hash of sorted chunk ID set for cross-store comparison."""
    return _hashlib.sha256(
        "|".join(sorted(chunk_ids)).encode()
    ).hexdigest()


async def _create_generation(gen_id: str, doc_id: str) -> None:
    """Create a generation record in PREPARING state."""
    from sqlalchemy import text as sa_text
    async with async_session() as session:
        conn = await session.connection()
        await conn.execute(sa_text(
            "INSERT INTO index_generations (id, doc_id, status, created_at) "
            "VALUES (:id, :did, 'preparing', datetime('now'))"
        ), {"id": gen_id, "did": doc_id})
        await session.commit()


async def _update_generation_status(gen_id: str, status: str) -> None:
    """Update generation status during pipeline stages."""
    from sqlalchemy import text as sa_text
    async with async_session() as session:
        conn = await session.connection()
        await conn.execute(sa_text(
            "UPDATE index_generations SET status=:st WHERE id=:id"
        ), {"id": gen_id, "st": status})
        await session.commit()


async def _verify_generation(gen_id: str, qdrant_ids: set[str], bm25_ids: set[str]) -> bool:
    """Compare chunk_id sets from Qdrant and BM25. Return True if identical."""
    if qdrant_ids != bm25_ids:
        missing_in_bm25 = qdrant_ids - bm25_ids
        missing_in_qdrant = bm25_ids - qdrant_ids
        logger.error(
            "generation verify failed gen_id=%s qdrant=%d bm25=%d missing_bm25=%d missing_qdrant=%d",
            gen_id[:8], len(qdrant_ids), len(bm25_ids),
            len(missing_in_bm25), len(missing_in_qdrant),
        )
        return False
    return True


async def _commit_generation(gen_id: str, qdrant_count: int, bm25_count: int,
                             chunk_ids_hash: str) -> None:
    """Mark generation as COMMITTED with verified counts and hash."""
    from sqlalchemy import text as sa_text
    async with async_session() as session:
        conn = await session.connection()
        await conn.execute(sa_text(
            "UPDATE index_generations SET status='committed', vector_chunk_count=:vc, "
            "bm25_count=:bc, chunk_ids_hash=:hash, chunk_ids_consistent=1, "
            "committed_at=datetime('now') WHERE id=:id"
        ), {"id": gen_id, "vc": qdrant_count, "bc": bm25_count, "hash": chunk_ids_hash})
        await session.commit()


async def _fail_generation(gen_id: str, qdrant_count: int, bm25_count: int,
                           error_stage: str = "", error_message: str = "") -> None:
    """Mark generation as FAILED with error context."""
    from sqlalchemy import text as sa_text
    async with async_session() as session:
        conn = await session.connection()
        await conn.execute(sa_text(
            "UPDATE index_generations SET status='failed', vector_chunk_count=:vc, "
            "bm25_count=:bc, error_stage=:stage, error_message=:msg WHERE id=:id"
        ), {"id": gen_id, "vc": qdrant_count, "bc": bm25_count,
            "stage": error_stage, "msg": error_message})
        await session.commit()


async def _switch_active_generation(doc_id: str, gen_id: str) -> None:
    """Set the active_generation_id on the document within a transaction."""
    from sqlalchemy import text as sa_text
    async with async_session() as session:
        conn = await session.connection()
        await conn.execute(sa_text(
            "UPDATE documents SET active_generation_id=:gid WHERE id=:did"
        ), {"gid": gen_id, "did": doc_id})
        await session.commit()


async def cleanup_staging_generations() -> int:
    """On startup: clean up documents with staging generations (crashed mid-index).
    Returns count of cleaned generations.
    """
    from sqlalchemy import text as sa_text

    from textdb.bm25_search import BM25Search
    from vectordb.factory import create_vectordb

    async with async_session() as session:
        conn = await session.connection()
        rows = (await conn.execute(sa_text(
            "SELECT g.id, g.doc_id, d.active_generation_id "
            "FROM index_generations g "
            "LEFT JOIN documents d ON d.id = g.doc_id "
            "WHERE g.status IN ('preparing', 'staging')"
        ))).fetchall()

    if not rows:
        return 0

    try:
        vectordb = await create_vectordb()
    except Exception as e:
        logger.warning("cleanup_staging: vectordb unavailable, skipping vector cleanup: %s", e)
        vectordb = None

    fts = BM25Search()
    for gen_id, doc_id, active_generation_id in rows:
        try:
            # A stale attempt can coexist with a newer committed generation.
            # Deleting by document_id here would destroy the active generation's
            # Qdrant and BM25 data during the next restart.
            if active_generation_id and active_generation_id != gen_id:
                await _fail_generation(
                    gen_id, 0, 0,
                    error_stage="startup_cleanup",
                    error_message="Superseded by active generation",
                )
                logger.warning(
                    "marked superseded staging generation failed without deleting active data "
                    "gen_id=%s doc_id=%s active_gen_id=%s",
                    gen_id[:8], doc_id, active_generation_id[:8],
                )
                continue
            if vectordb is not None:
                await vectordb.delete_by_document(doc_id)
            await fts.delete_by_document(doc_id)
            await _fail_generation(gen_id, 0, 0)
            logger.warning("cleaned up staging generation gen_id=%s doc_id=%s", gen_id[:8], doc_id)
        except Exception as e:
            logger.error("failed to clean staging gen_id=%s: %s", gen_id[:8], e)

    return len(rows)


def _document_key(doc_id: str, filename: str) -> str:
    """Derive a stable document_key from filename (same algorithm as qrels_schema)."""
    import re
    base = filename.rsplit(".", 1)[0] if "." in filename else filename
    return re.sub(r"[^a-zA-Z0-9-]", "-", base).strip("-").lower() or doc_id[:8]


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

    return await _run_document_ingestion(
        doc_id, filename, file_path, file_type, background
    )


async def ingest_document_from_path(
    filename: str,
    temp_path: str,
    file_hash: str,
    file_size: int,
    file_type: str,
    background: bool = False,
) -> str:
    """Register and ingest a file that has already been streamed to disk."""
    from sqlalchemy.exc import IntegrityError

    async with async_session() as session:
        result = await session.execute(
            select(Document).where(Document.file_hash == file_hash)
        )
        if result.scalar_one_or_none():
            delete_file(temp_path)
            raise ValueError(f"File '{filename}' already exists (hash matched)")

    try:
        file_path = finalize_upload(temp_path, filename)
    except Exception:
        delete_file(temp_path)
        raise
    doc_id = str(uuid.uuid4())
    try:
        async with async_session() as session:
            session.add(Document(
                id=doc_id,
                filename=filename,
                file_hash=file_hash,
                file_size=file_size,
                file_type=file_type,
                status=DocStatus.uploaded,
            ))
            await session.commit()
    except IntegrityError as exc:
        delete_file(file_path)
        raise ValueError(f"File '{filename}' already exists (hash matched)") from exc
    except Exception:
        delete_file(file_path)
        raise

    return await _run_document_ingestion(
        doc_id, filename, file_path, file_type, background
    )


async def _run_document_ingestion(
    doc_id: str,
    filename: str,
    file_path: str,
    file_type: str,
    background: bool,
) -> str:
    """Start document processing after file and database record are durable."""
    if background:
        async def _bg_process():
            sem = _get_semaphore()
            async with sem:
                started = time.time()
                logger.info("ingestion started doc_id=%s filename=%s", doc_id, filename)
                # ── Idempotency guard: skip if already committed ──
                from models.orm import IndexGeneration, GenerationStatus
                async with async_session() as session:
                    doc = (await session.execute(
                        select(Document).where(Document.id == doc_id)
                    )).scalar_one()
                    if doc.active_generation_id:
                        gen = (await session.execute(
                            select(IndexGeneration).where(
                                IndexGeneration.id == doc.active_generation_id
                            )
                        )).scalar_one_or_none()
                        if gen and gen.status == GenerationStatus.committed:
                            if doc.status != DocStatus.ready:
                                doc.status = DocStatus.ready
                                await session.commit()
                            logger.info(
                                "ingestion skipped: generation already committed "
                                "doc_id=%s gen_id=%s", doc_id, doc.active_generation_id[:8],
                            )
                            return

                from rag.progress import progress
                for attempt in range(settings.ingestion_max_retries):
                    try:
                        await _process_document(doc_id, file_path, file_type)
                        elapsed = (time.time() - started) * 1000
                        logger.info(
                            "ingestion complete doc_id=%s elapsed_ms=%d attempt=%d",
                            doc_id, int(elapsed), attempt,
                        )
                        from metrics import get_metrics
                        get_metrics().record_ingestion(success=True, latency_ms=elapsed)
                        return
                    except Exception as e:
                        error_type = _classify_error(e)
                        is_last = (attempt == settings.ingestion_max_retries - 1)
                        if error_type == "permanent" or is_last:
                            async with async_session() as session:
                                doc = (await session.execute(
                                    select(Document).where(Document.id == doc_id)
                                )).scalar_one()
                                doc.status = DocStatus.failed
                                doc.error_message = str(e)[:500]
                                await session.commit()
                            logger.error(
                                "ingestion failed doc_id=%s type=%s error=%s",
                                doc_id, error_type, str(e)[:200],
                            )
                            from metrics import get_metrics
                            get_metrics().record_ingestion(
                                success=False, latency_ms=(time.time() - started) * 1000,
                            )
                            progress.publish(doc_id, {
                                "status": "failed", "error": str(e)[:200],
                            })
                            return
                        delay = _retry_delay(attempt, error_type)
                        logger.warning(
                            "ingestion retry doc_id=%s attempt=%d/%d type=%s delay=%.1fs error=%s",
                            doc_id, attempt + 1, settings.ingestion_max_retries,
                            error_type, delay, str(e)[:200],
                        )
                        await asyncio.sleep(delay)
        from worker.tasks import get_task_manager
        get_task_manager().create(
            _bg_process,
            f"ingestion_{doc_id[:8]}",
            metadata={"doc_id": doc_id},
        )
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


async def recover_incomplete_documents() -> int:
    """Reschedule durable, non-terminal documents after a service restart."""
    incomplete = (
        DocStatus.uploaded,
        DocStatus.parsing,
        DocStatus.chunking,
        DocStatus.embedding,
        DocStatus.indexing,
    )
    async with async_session() as session:
        result = await session.execute(
            select(Document).where(Document.status.in_(incomplete))
        )
        documents = list(result.scalars().all())

        recoverable: list[tuple[str, str, str, str]] = []
        for doc in documents:
            file_path = find_upload(doc.filename, doc.file_type)
            if file_path is None:
                doc.status = DocStatus.failed
                doc.error_message = "服务重启后无法恢复：原始文件不存在"
                continue
            doc.status = DocStatus.uploaded
            doc.error_message = None
            recoverable.append((doc.id, doc.filename, file_path, doc.file_type))
        await session.commit()

    scheduled = 0
    for doc_id, filename, file_path, file_type in recoverable:
        try:
            await _run_document_ingestion(
                doc_id, filename, file_path, file_type, background=True
            )
            scheduled += 1
        except Exception as exc:
            async with async_session() as session:
                result = await session.execute(
                    select(Document).where(Document.id == doc_id)
                )
                failed_doc = result.scalar_one_or_none()
                if failed_doc is not None:
                    failed_doc.status = DocStatus.failed
                    failed_doc.error_message = f"恢复任务调度失败: {str(exc)[:300]}"
                    await session.commit()
    return scheduled


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

        # ── Index: multi-stage atomic indexing ──
        doc.status = DocStatus.indexing
        await session.commit()
        progress.publish(doc_id, {"status": "indexing", "message": "正在写入索引..."})
        t_idx = time.time()

        gen_id = str(uuid.uuid4())
        await _create_generation(gen_id, doc_id)

        vectordb = await create_vectordb()
        fts = BM25Search()

        # Stage 1: Clean up old data
        await vectordb.delete_by_document(doc_id)
        await fts.delete_by_document(doc_id)

        # Stage 2: Build points and FTS entries with stable chunk_ids
        points = []
        doc_key = _document_key(doc_id, doc.filename)
        expected_chunk_ids: set[str] = set()
        for chunk, vector in zip(chunks, vectors, strict=False):
            chunk_id = str(uuid.uuid5(uuid.NAMESPACE_DNS, f"{doc_id}:{chunk.chunk_index}"))
            expected_chunk_ids.add(chunk_id)
            points.append({
                "id": chunk_id,
                "vector": vector,
                "payload": {
                    "document_id": doc_id,
                    "document_key": doc_key,
                    "chunk_index": chunk.chunk_index,
                    "text": chunk.text,
                    "section_key": chunk.section_key,
                    "generation_id": gen_id,
                },
            })

        fts_entries = [
            (str(uuid.uuid5(uuid.NAMESPACE_DNS, f"{doc_id}:{c.chunk_index}")),
             doc_id, doc_key, c.section_key, c.chunk_index, c.text)
            for c in chunks
        ]

        expected_count = len(chunks)
        expected_hash = _hash_chunk_ids(expected_chunk_ids)

        try:
            # Stage 3: Write Qdrant
            await _update_generation_status(gen_id, "writing_vector")
            await vectordb.upsert(points)

            # Stage 4: Write BM25
            await _update_generation_status(gen_id, "writing_bm25")
            await fts.insert_batch(fts_entries)

            # Stage 5: Verify cross-store consistency
            await _update_generation_status(gen_id, "verifying")
            qdrant_read_ids: set[str] = set()
            try:
                stored = await vectordb.get_chunk_ids_by_document(doc_id)
                qdrant_read_ids = set(stored)
            except Exception:
                pass
            fts_read_ids = set(await fts.get_chunk_ids_by_document(doc_id))

            if not await _verify_generation(gen_id, qdrant_read_ids, fts_read_ids):
                await _fail_generation(
                    gen_id, len(qdrant_read_ids), len(fts_read_ids),
                    error_stage="verifying",
                    error_message=f"Cross-store mismatch: Qdrant={len(qdrant_read_ids)} BM25={len(fts_read_ids)}",
                )
                raise RuntimeError(
                    f"Generation {gen_id[:8]} verification failed: "
                    f"Qdrant={len(qdrant_read_ids)} BM25={len(fts_read_ids)}"
                )

            # Stage 6: Commit and switch
            await _commit_generation(gen_id, len(qdrant_read_ids), len(fts_read_ids), expected_hash)
            await _switch_active_generation(doc_id, gen_id)

            # Done — finalize document in same try block for consistency
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

            # V4: Invalidate answer cache on document change
            try:
                from rag.answer_cache import bump_collection_version
                bump_collection_version()
            except Exception:
                pass

            idx_elapsed = int((time.time() - t_idx) * 1000)
            logger.info("indexing done doc_id=%s elapsed_ms=%d gen_id=%s chunks=%d",
                        doc_id, idx_elapsed, gen_id[:8], expected_count)
        except Exception:
            # Clean up Qdrant data written before the failure
            try:
                await vectordb.delete_by_document(doc_id)
            except Exception as cleanup_err:
                logger.warning("failed to clean Qdrant after indexing failure: %s", cleanup_err)
            await _fail_generation(
                gen_id, 0, 0,
                error_stage="indexing",
                error_message="Indexing failed, see logs for details",
            )
            raise
