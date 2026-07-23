import asyncio
import logging
import os
from contextlib import asynccontextmanager, suppress
from pathlib import Path

from fastapi import Depends, FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from limiter import limiter
from security import jwt_auth, require_admin
from slowapi.errors import RateLimitExceeded
from slowapi.middleware import SlowAPIMiddleware

from config import settings
from models.database import check_revision_gate, init_db

logger = logging.getLogger(__name__)


from datetime import UTC, datetime, timedelta

from sqlalchemy import select, update

from models.database import session_scope
from models.orm import DocStatus, Document


async def _cleanup_stuck_documents():
    """Mark documents stuck in intermediate states > 30 min as failed."""
    cutoff = datetime.now(UTC) - timedelta(minutes=30)
    stuck_statuses = (DocStatus.parsing, DocStatus.chunking, DocStatus.embedding, DocStatus.indexing)
    async with session_scope() as session:
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


async def _bootstrap_user() -> None:
    """Create the first system administrator, auto-generating credentials if needed."""
    import re
    import secrets as _secrets

    from auth.jwt import hash_password
    from config import _write_env_key
    from sqlalchemy import select

    from models.database import session_scope
    from models.orm import User

    async with session_scope() as session:
        result = await session.execute(select(User).limit(1))
        if result.scalar_one_or_none():
            return

        username = settings.bootstrap_admin_username.strip()
        password = settings.bootstrap_admin_password
        if not re.fullmatch(r"[A-Za-z0-9_.-]{3,64}", username):
            raise RuntimeError(
                "BOOTSTRAP_ADMIN_USERNAME must be 3-64 characters using "
                "letters, numbers, '.', '_' or '-'"
            )
        if not password:
            password = _secrets.token_urlsafe(16)
            env_path = Path(str(settings.model_config.get("env_file", ".env")))
            _write_env_key(env_path, "BOOTSTRAP_ADMIN_PASSWORD", password)
            logger.warning(
                "Auto-generated BOOTSTRAP_ADMIN_PASSWORD and saved to %s. "
                "Use this password to log in as '%s', then change it immediately.",
                env_path, username,
            )
            print(
                f"\n{'='*60}\n"
                f"  FIRST RUN: admin account created\n"
                f"  Username: {username}\n"
                f"  Password: {password}\n"
                f"  Saved to: {env_path}\n"
                f"  Change this password after logging in!\n"
                f"{'='*60}\n",
                flush=True,
            )
        elif len(password) < 12 or len(password.encode("utf-8")) > 72:
            actual = len(password.encode("utf-8"))
            reason = f"got {actual} byte" + ("s" if actual != 1 else "")
            raise RuntimeError(
                "No users exist. Set BOOTSTRAP_ADMIN_PASSWORD to a unique "
                f"12-72 byte password before first startup ({reason}). "
                "If you already set it in .env and it appears empty, "
                "check that the file is in the backend/ directory."
            )

        import uuid as _uuid
        admin_id = str(_uuid.uuid4())
        session.add(User(
            id=admin_id,
            username=username,
            password_hash=hash_password(password),
            role="system_admin",
        ))
        await session.commit()
        logger.info("Bootstrap system administrator created: username=%s", username)


@asynccontextmanager
async def lifespan(app: FastAPI):
    from logging_config import setup_logging
    setup_logging()

    from auth.jwt import validate_jwt_configuration
    validate_jwt_configuration()

    # Verify database schema revision matches code before any DB access
    await check_revision_gate()
    await init_db()

    # Bootstrap default admin user if no users exist
    await _bootstrap_user()

    Path(settings.upload_dir).mkdir(parents=True, exist_ok=True)
    # Clean up leftover restore artifacts (candidate/previous dirs) from interrupted restores
    from api.backup import _cleanup_orphan_qdrant_collections, _cleanup_restore_artifacts
    upload_path = Path(settings.upload_dir).resolve()
    _cleanup_restore_artifacts(upload_path)
    # Audit Qdrant collections for orphan restore temp collections
    active_collection = settings.qdrant_active_collection or settings.qdrant_collection
    await _cleanup_orphan_qdrant_collections(active_collection)
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
    # Auto-detect actual embedding dimension from API and update config
    try:
        from api.settings import _get_actual_embedding_dim
        actual_dim = await _get_actual_embedding_dim()
        if actual_dim != settings.embedding_dim:
            logger.info("embedding dim auto-detected: config=%d -> actual=%d, updating",
                        settings.embedding_dim, actual_dim)
            settings.embedding_dim = actual_dim
        else:
            logger.info("embedding dim verified: %d matches config", actual_dim)
    except Exception as e:
        logger.warning("embedding dim auto-detection skipped: %s", e)
    # Clean up orphaned staging generations (crashed mid-index)
    from rag.pipeline import cleanup_staging_generations, recover_incomplete_documents
    cleaned_gens = await cleanup_staging_generations()
    if cleaned_gens:
        logger.info("cleaned up %d orphaned staging generations", cleaned_gens)
    recovered_docs = await recover_incomplete_documents()
    if recovered_docs:
        logger.info("rescheduled %d incomplete documents", recovered_docs)
    # Recover stale background tasks (crashed mid-execution)
    import worker.task_handlers  # noqa: F401  # register durable handlers
    from worker.tasks import get_task_manager, recover_tasks_on_startup
    recovered = await recover_tasks_on_startup()
    get_task_manager().start_recovery_monitor()
    if recovered:
        logger.info("recovered %d stale background tasks", recovered)
    # Preload reranker model in background
    if settings.hf_endpoint:
        os.environ["HF_ENDPOINT"] = settings.hf_endpoint
    from reranker.factory import preload_reranker_async
    preload_reranker_async()
    from ocr.factory import preload_ocr_async
    preload_ocr_async()
    from optional_models import monitor_optional_models
    optional_model_monitor = asyncio.create_task(
        monitor_optional_models(), name="optional-model-monitor"
    )
    # 重建用户画像 Qdrant 索引
    from memory.profile import rebuild_index
    await rebuild_index()
    try:
        yield
    finally:
        optional_model_monitor.cancel()
        with suppress(asyncio.CancelledError):
            await optional_model_monitor
        # Shutdown: cancel pending background tasks
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


@app.get("/api/metrics/prometheus")
async def metrics_prometheus(_admin: None = Depends(require_admin)):
    """Prometheus text format metrics endpoint."""
    from fastapi.responses import PlainTextResponse
    from metrics import export_prometheus
    return PlainTextResponse(content=export_prometheus(), media_type="text/plain; charset=utf-8")


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
    optional_models: dict[str, dict] = {}

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
        optional_models["reranker"] = get_reranker_status()
        deps["reranker"] = optional_models["reranker"]["status"]
    except Exception:
        deps["reranker"] = "error"

    # OCR status
    try:
        from ocr.factory import get_ocr_status
        optional_models["ocr"] = get_ocr_status()
        deps["ocr"] = optional_models["ocr"]["status"]
    except Exception:
        deps["ocr"] = "error"

    # Aggregate status
    # Optional model failures degrade quality/features but never make the core
    # HTTP service unhealthy. Only required storage dependencies are fatal.
    has_error = any(deps[name] == "error" for name in ("sqlite", "qdrant"))
    degraded_states = {
        "error", "failed", "missing_api_key", "missing_dependency", "downloading", "loading"
    }
    has_degraded = any(v in degraded_states for v in deps.values()) or has_error

    if has_error:
        status = "error"
    elif has_degraded:
        status = "degraded"
    else:
        status = "ok"

    return {
        "status": status,
        "dependencies": deps,
        "optional_models": optional_models,
        "core_ready": not has_error,
    }


@app.get("/api/health/tasks")
async def health_tasks():
    """Return background task status — running tasks and recent history."""
    from worker.tasks import get_task_manager
    return get_task_manager().get_status()


from api.documents import router as documents_router

app.include_router(documents_router, dependencies=[Depends(jwt_auth)])

from api.chat import router as chat_router

app.include_router(chat_router, dependencies=[Depends(jwt_auth)])

from api.conversations import router as conversations_router

app.include_router(conversations_router, dependencies=[Depends(jwt_auth)])

from api.settings import router as settings_router

app.include_router(settings_router, dependencies=[Depends(jwt_auth)])

from api.memories import router as memories_router

app.include_router(memories_router, dependencies=[Depends(jwt_auth)])

from api.backup import router as backup_router

app.include_router(backup_router, dependencies=[Depends(jwt_auth)])

# Auth routes (public — no auth dependency)
from api.auth import router as auth_router

app.include_router(auth_router)

# User management routes (system_admin only — role enforced per-endpoint)
from api.users import router as users_router

app.include_router(users_router, dependencies=[Depends(jwt_auth)])

from api.audit import router as audit_router

app.include_router(audit_router, dependencies=[Depends(jwt_auth)])

# ── Metrics (no auth required — Prometheus scrape endpoint) ──
from api.metrics import router as metrics_router

app.include_router(metrics_router)

if __name__ == "__main__":
    import uvicorn

    host = settings.server_host
    if settings.allow_remote_access:
        host = "0.0.0.0"

    uvicorn.run(app, host=host, port=8000)
