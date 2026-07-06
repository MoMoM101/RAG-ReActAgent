import os
import logging
from pathlib import Path
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from slowapi.middleware import SlowAPIMiddleware
from slowapi.errors import RateLimitExceeded
from config import settings
from models.database import init_db
from limiter import limiter

logger = logging.getLogger(__name__)


from datetime import datetime, timedelta, timezone

from models.database import async_session
from models.orm import Document, DocStatus
from sqlalchemy import select, update


async def _cleanup_stuck_documents():
    """Mark documents stuck in intermediate states > 30 min as failed."""
    cutoff = datetime.now(timezone.utc) - timedelta(minutes=30)
    stuck_statuses = (DocStatus.parsing, DocStatus.chunking, DocStatus.embedding, DocStatus.indexing)
    async with async_session() as session:
        result = await session.execute(
            select(Document.id, Document.filename)
            .where(Document.status.in_(stuck_statuses))
            .where(Document.updated_at < cutoff)
        )
        stuck = result.fetchall()
        if stuck:
            await session.execute(
                update(Document)
                .where(Document.id.in_([r[0] for r in stuck]))
                .values(status=DocStatus.failed, error_message="入库超时未完成，自动标记为失败")
            )
            await session.commit()
            for doc_id, filename in stuck:
                logger.warning("stale doc cleaned doc_id=%s filename=%s", doc_id, filename)


@asynccontextmanager
async def lifespan(app: FastAPI):
    from logging_config import setup_logging
    setup_logging()
    Path(settings.upload_dir).mkdir(parents=True, exist_ok=True)
    await init_db()
    # 恢复 active collection 指针（rebuild 持久化的）
    _ptr = Path(settings.qdrant_path) / "active_collections.json"
    if _ptr.exists():
        try:
            import json as _json
            _data = _json.loads(_ptr.read_text())
            settings.qdrant_active_collection = _data.get("rag_chunks", "")
            import memory.profile as _profile_mod
            _profile_mod.PROFILE_COLLECTION = _data.get("user_profile", "user_profile")
            logger.info("active collections restored: chunks=%s profile=%s",
                        _data.get("rag_chunks"), _data.get("user_profile"))
        except Exception:
            pass
    # Auto-detect actual embedding dimension from API
    from embedding.factory import create_embedding
    emb = create_embedding()
    test_vec = await emb.embed_query("dim check")
    detected_dim = len(test_vec)
    settings.embedding_dim = detected_dim
    logger.info("embedding dim detected: %d", detected_dim)
    # Warn if existing Qdrant collection dimension doesn't match
    try:
        from vectordb.qdrant import QdrantVectorDB
        chunks_db = QdrantVectorDB()
        if await chunks_db.collection_exists():
            col_dim = await chunks_db.get_collection_dim()
            if col_dim is not None and col_dim != detected_dim:
                logger.warning(
                    "embedding dim mismatch: API=%d Qdrant=%d. Use /api/settings/rebuild-collections to migrate",
                    detected_dim, col_dim,
                )
    except Exception:
        logger.warning("Qdrant dimension check skipped, service unavailable", exc_info=True)
    # Clean up documents stuck in intermediate states > 30 min
    await _cleanup_stuck_documents()
    # Preload reranker model in background
    import os
    if settings.hf_endpoint:
        os.environ["HF_ENDPOINT"] = settings.hf_endpoint
    from reranker.factory import preload_reranker_async
    preload_reranker_async()
    from ocr.factory import preload_ocr_async
    preload_ocr_async()
    # 重建用户画像 Qdrant 索引
    from memory.profile import rebuild_index
    await rebuild_index()
    yield

app = FastAPI(title="RAG Agent", lifespan=lifespan)
app.state.limiter = limiter

# Rate limit exceeded → 429 JSON response
@app.exception_handler(RateLimitExceeded)
async def _rate_limit_handler(request: Request, exc: RateLimitExceeded):
    return JSONResponse(
        status_code=429,
        content={"detail": "请求过于频繁，请稍后再试"},
    )

# Structured request logging
from middleware.logging import RequestIDMiddleware
app.add_middleware(RequestIDMiddleware)
app.add_middleware(SlowAPIMiddleware)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/api/health")
async def health():
    return {"status": "ok"}

from api.documents import router as documents_router
app.include_router(documents_router)

from api.chat import router as chat_router
app.include_router(chat_router)

from api.conversations import router as conversations_router
app.include_router(conversations_router)

from api.settings import router as settings_router
app.include_router(settings_router)

from api.memories import router as memories_router
app.include_router(memories_router)

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
