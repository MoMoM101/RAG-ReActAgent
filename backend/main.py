import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Depends, FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from limiter import limiter
from security import require_admin
from slowapi.errors import RateLimitExceeded
from slowapi.middleware import SlowAPIMiddleware

from config import settings
from models.database import init_db

logger = logging.getLogger(__name__)


from datetime import UTC, datetime, timedelta

from sqlalchemy import select, update

from models.database import async_session
from models.orm import DocStatus, Document


async def _cleanup_stuck_documents():
    """Mark documents stuck in intermediate states > 30 min as failed."""
    cutoff = datetime.now(UTC) - timedelta(minutes=30)
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


def _bootstrap_admin_token() -> None:
    """Generate and persist an admin token on first startup if none is configured."""
    from pathlib import Path as _Path

    if settings.admin_api_token:
        return

    env_path = _Path(str(settings.model_config.get("env_file", ".env")))
    if env_path.exists():
        content = env_path.read_text(encoding="utf-8")
        import re as _re
        m = _re.search(r"^ADMIN_API_TOKEN=(.+)", content, _re.MULTILINE)
        if m and m.group(1).strip():
            settings.admin_api_token = m.group(1).strip()
            return

    from security import generate_admin_token
    token = generate_admin_token()
    settings.admin_api_token = token

    # Write token to .env file
    if env_path.exists():
        lines = env_path.read_text(encoding="utf-8").splitlines()
        new_lines = []
        found = False
        for line in lines:
            if line.strip().startswith("ADMIN_API_TOKEN="):
                new_lines.append(f"ADMIN_API_TOKEN={token}")
                found = True
            else:
                new_lines.append(line)
        if not found:
            new_lines.append(f"ADMIN_API_TOKEN={token}")
        env_path.write_text("\n".join(new_lines) + "\n", encoding="utf-8")
    else:
        env_path.write_text(f"ADMIN_API_TOKEN={token}\n", encoding="utf-8")

    logger.info("Generated new admin API token (saved to %s)", env_path)


@asynccontextmanager
async def lifespan(app: FastAPI):
    from logging_config import setup_logging
    setup_logging()

    # Bootstrap admin token on first run
    _bootstrap_admin_token()

    Path(settings.upload_dir).mkdir(parents=True, exist_ok=True)
    # Clean up leftover restore artifacts (candidate/previous dirs) from interrupted restores
    from api.backup import _cleanup_orphan_qdrant_collections, _cleanup_restore_artifacts
    upload_path = Path(settings.upload_dir).resolve()
    _cleanup_restore_artifacts(upload_path)
    # Audit Qdrant collections for orphan restore temp collections
    active_collection = settings.qdrant_active_collection or settings.qdrant_collection
    await _cleanup_orphan_qdrant_collections(active_collection)
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
    logger.info("embedding dim (from config): %d", settings.embedding_dim)
    # Clean up documents stuck in intermediate states > 30 min
    await _cleanup_stuck_documents()
    # Preload reranker model in background
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
    # Shutdown: cancel pending background tasks
    from worker.tasks import get_task_manager
    await get_task_manager().shutdown()

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

# Maintenance middleware: return 503 for write requests during restore/rebuild
from fastapi.responses import JSONResponse as _JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware


class MaintenanceMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        if request.url.path.startswith("/api/health"):
            return await call_next(request)
        if request.method in ("POST", "PUT", "DELETE", "PATCH"):
            from maintenance import get_maintenance_state
            mstate = get_maintenance_state()
            if mstate.active:
                return _JSONResponse(
                    status_code=503,
                    content={
                        "detail": f"系统维护中（{mstate.phase.value}），请稍后重试",
                        "phase": mstate.phase.value,
                        "progress_pct": mstate.progress_pct,
                    },
                    headers={"Retry-After": "30"},
                )
        return await call_next(request)


app.add_middleware(MaintenanceMiddleware)

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


@app.get("/api/metrics")
async def metrics(_admin: None = Depends(require_admin)):
    """Structured metrics endpoint — no raw queries, content, or API keys."""
    from metrics import get_metrics
    return get_metrics().snapshot()


@app.get("/api/health/dependencies")
async def health_dependencies():
    """Report health status of each dependency without blocking startup."""
    deps: dict[str, str] = {
        "sqlite": "ok",
        "qdrant": "ok",
        "embedding": "ok",
        "llm": "ok",
        "reranker": "disabled",
        "ocr": "disabled",
    }

    # Check SQLite
    try:
        from sqlalchemy import text as sa_text

        from models.database import engine
        async with engine.begin() as conn:
            await conn.execute(sa_text("SELECT 1"))
    except Exception:
        deps["sqlite"] = "error"

    # Check Qdrant
    try:
        from vectordb.qdrant import QdrantVectorDB
        vdb = QdrantVectorDB()
        await vdb.collection_exists()
    except Exception:
        deps["qdrant"] = "error"

    # Check embedding (just check if key is configured)
    if not settings.embedding_api_key and not settings.llm_api_key:
        deps["embedding"] = "missing_api_key"

    # Check LLM (just check if key is configured)
    if not settings.llm_api_key:
        deps["llm"] = "missing_api_key"

    # Reranker status
    try:
        from reranker.factory import get_reranker_status
        deps["reranker"] = get_reranker_status()["status"]
    except Exception:
        deps["reranker"] = "error"

    # OCR status
    try:
        from ocr.factory import get_ocr_status
        deps["ocr"] = get_ocr_status()["status"]
    except Exception:
        deps["ocr"] = "error"

    # Aggregate status
    error_states = {"error", "failed"}
    degraded_states = {"missing_api_key", "missing_dependency", "loading"}
    has_error = any(v in error_states for v in deps.values())
    has_degraded = any(v in degraded_states for v in deps.values()) or has_error

    if has_error:
        status = "error"
    elif has_degraded:
        status = "degraded"
    else:
        status = "ok"

    return {"status": status, "dependencies": deps}


@app.get("/api/health/tasks")
async def health_tasks():
    """Return background task status — running tasks and recent history."""
    from worker.tasks import get_task_manager
    return get_task_manager().get_status()


from api.documents import router as documents_router

app.include_router(documents_router, dependencies=[Depends(require_admin)])

from api.chat import router as chat_router

app.include_router(chat_router, dependencies=[Depends(require_admin)])

from api.conversations import router as conversations_router

app.include_router(conversations_router, dependencies=[Depends(require_admin)])

from api.settings import router as settings_router

app.include_router(settings_router, dependencies=[Depends(require_admin)])

from api.memories import router as memories_router

app.include_router(memories_router, dependencies=[Depends(require_admin)])

from api.backup import router as backup_router

app.include_router(backup_router, dependencies=[Depends(require_admin)])

if __name__ == "__main__":
    import uvicorn

    host = settings.server_host
    if settings.allow_remote_access:
        host = "0.0.0.0"

    if settings.admin_api_token:
        logger.info("Admin API token is configured")
    else:
        logger.warning(
            "ADMIN_API_TOKEN is empty — admin endpoints are unprotected. "
            "Set ADMIN_API_TOKEN in .env or environment to enable authentication."
        )

    uvicorn.run(app, host=host, port=8000)
