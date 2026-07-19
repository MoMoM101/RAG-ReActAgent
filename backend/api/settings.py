import asyncio
import contextlib
import json
import logging
import os
import re
from pathlib import Path

import httpx
from fastapi import APIRouter
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, field_validator

from config import settings

router = APIRouter(prefix="/api/settings", tags=["settings"])
_V2_SUFFIX = "_v2"


def _derive_doc_key(doc_id: str, filename: str) -> str:
    """Derive the stable retrieval document key without importing the RAG pipeline."""
    base = filename.rsplit(".", 1)[0] if "." in filename else filename
    return re.sub(r"[^a-zA-Z0-9-]", "-", base).strip("-").lower() or doc_id[:8]


async def _cleanup_bm25_v2() -> None:
    """清除 BM25 双缓冲重建的 _v2 残留表。"""
    from sqlalchemy import text as sa_text

    from models.database import engine
    async with engine.begin() as conn:
        for t in ("bm25_docs_v2", "bm25_index_v2", "bm25_stats_v2", "chunks_fts_v2"):
            await conn.execute(sa_text(f"DROP TABLE IF EXISTS {t}"))


async def _switch_bm25_tables() -> None:
    """Atomically replace live BM25 tables with a validated v2 buffer."""
    from sqlalchemy import text as sa_text

    from models.database import engine
    async with engine.begin() as conn:
        table_names = ("bm25_docs", "bm25_index", "bm25_stats")
        result = await conn.execute(
            sa_text(
                "SELECT name FROM sqlite_master "
                "WHERE type = 'table' AND name IN "
                "('bm25_docs_v2', 'bm25_index_v2', 'bm25_stats_v2')"
            )
        )
        staged = {row[0] for row in result.fetchall()}
        missing = [f"{name}_v2" for name in table_names if f"{name}_v2" not in staged]
        if missing:
            raise RuntimeError(f"BM25 staged tables missing: {', '.join(missing)}")

        live_result = await conn.execute(
            sa_text(
                "SELECT name FROM sqlite_master "
                "WHERE type = 'table' AND name IN "
                "('bm25_docs', 'bm25_index', 'bm25_stats')"
            )
        )
        live = {row[0] for row in live_result.fetchall()}

        # SQLite DDL is transactional here. Keep live tables as _old until all
        # staged tables have been promoted, then remove the backup set.
        for index_name in (
            "idx_bm25_docs_did", "idx_bm25_index_term",
            "idx_bm25_docs_v2_did", "idx_bm25_index_v2_term",
        ):
            await conn.execute(sa_text(f"DROP INDEX IF EXISTS {index_name}"))
        for name in table_names:
            await conn.execute(sa_text(f"DROP TABLE IF EXISTS {name}_old"))
            if name in live:
                await conn.execute(sa_text(f"ALTER TABLE {name} RENAME TO {name}_old"))
        for name in table_names:
            await conn.execute(sa_text(f"ALTER TABLE {name}_v2 RENAME TO {name}"))
        for name in table_names:
            await conn.execute(sa_text(f"DROP TABLE IF EXISTS {name}_old"))
        await conn.execute(
            sa_text("CREATE INDEX idx_bm25_docs_did ON bm25_docs(document_id)")
        )
        await conn.execute(
            sa_text("CREATE INDEX idx_bm25_index_term ON bm25_index(term)")
        )

logger = logging.getLogger(__name__)

ENV_PATH = Path(__file__).resolve().parent.parent / ".env"


class LLMSettings(BaseModel):
    provider: str = "openai"
    model: str = "gpt-4o"
    api_key: str = ""
    base_url: str = "https://api.openai.com/v1"

    @field_validator("provider")
    @classmethod
    def validate_provider(cls, v: str) -> str:
        if not re.fullmatch(r"[a-z0-9_-]+", v):
            raise ValueError("Provider must be alphanumeric")
        return v

    @field_validator("base_url")
    @classmethod
    def validate_base_url(cls, v: str) -> str:
        if v and not re.match(r"^https?://", v):
            raise ValueError("Base URL must start with http:// or https://")
        return v


class EmbeddingSettings(BaseModel):
    provider: str = "openai"
    model: str = "text-embedding-3-small"
    api_key: str = ""
    base_url: str = "https://api.openai.com/v1"

    @field_validator("provider")
    @classmethod
    def validate_provider(cls, v: str) -> str:
        if not re.fullmatch(r"[a-z0-9_-]+", v):
            raise ValueError("Provider must be alphanumeric")
        return v

    @field_validator("base_url")
    @classmethod
    def validate_base_url(cls, v: str) -> str:
        if v and not re.match(r"^https?://", v):
            raise ValueError("Base URL must start with http:// or https://")
        return v


class SettingsResponse(BaseModel):
    llm: LLMSettings
    embedding: EmbeddingSettings
    web_search_enabled: bool = True
    rerank_enabled: bool = False
    retrieval_top_k: int = 5
    web_search_max_results: int = 5
    chunk_size: int = 384
    chunk_overlap: int = 50


def _read_env() -> dict[str, str]:
    """Read .env file into a dict."""
    env = {}
    if ENV_PATH.exists():
        for line in ENV_PATH.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                key, _, value = line.partition("=")
                env[key.strip()] = value.strip()
    return env


def _write_env(env: dict[str, str]) -> None:
    """Write dict to .env file, preserving existing non-related keys."""
    existing = _read_env() if ENV_PATH.exists() else {}
    existing.update(env)
    lines = []
    for k, v in existing.items():
        # Quote values containing spaces, #, or special chars
        if any(c in v for c in (" ", "#", "=", '"', "'")):
            escaped = v.replace("\\", "\\\\").replace('"', '\\"')
            lines.append(f'{k}="{escaped}"')
        else:
            lines.append(f"{k}={v}")
    ENV_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8")


@router.get("")
async def get_settings() -> SettingsResponse:
    env = _read_env()
    return SettingsResponse(
        llm=LLMSettings(
            provider=env.get("LLM_PROVIDER", settings.llm_provider),
            model=env.get("LLM_MODEL", settings.llm_model),
            api_key=_mask_key(env.get("LLM_API_KEY", "")),
            base_url=env.get("LLM_BASE_URL", settings.llm_base_url),
        ),
        embedding=EmbeddingSettings(
            provider=env.get("EMBEDDING_PROVIDER", settings.embedding_provider),
            model=env.get("EMBEDDING_MODEL", settings.embedding_model),
            api_key=_mask_key(env.get("EMBEDDING_API_KEY", "")),
            base_url=env.get("EMBEDDING_BASE_URL", settings.embedding_base_url),
        ),
        web_search_enabled=_env_bool("WEB_SEARCH_ENABLED", settings.web_search_enabled),
        rerank_enabled=_env_bool("RERANK_ENABLED", settings.rerank_enabled),
        retrieval_top_k=int(env.get("RETRIEVAL_TOP_K", settings.retrieval_top_k)),
        web_search_max_results=int(env.get("WEB_SEARCH_MAX_RESULTS", settings.web_search_max_results)),
        chunk_size=int(env.get("CHUNK_SIZE", settings.chunk_size)),
        chunk_overlap=int(env.get("CHUNK_OVERLAP", settings.chunk_overlap)),
    )


@router.put("")
async def update_settings(body: SettingsResponse):
    global settings
    from utils.crypto import encrypt_if_needed

    env_updates = {
        "LLM_PROVIDER": body.llm.provider,
        "LLM_MODEL": body.llm.model,
        "LLM_BASE_URL": body.llm.base_url,
        "EMBEDDING_PROVIDER": body.embedding.provider,
        "EMBEDDING_MODEL": body.embedding.model,
        "EMBEDDING_BASE_URL": body.embedding.base_url,
    }
    # 仅当提供了真实的新 API Key（非掩码）时加密后写入
    if body.llm.api_key and "***" not in body.llm.api_key:
        env_updates["LLM_API_KEY"] = encrypt_if_needed(body.llm.api_key, settings.secret_key)
    if body.embedding.api_key and "***" not in body.embedding.api_key:
        env_updates["EMBEDDING_API_KEY"] = encrypt_if_needed(body.embedding.api_key, settings.secret_key)

    _write_env({
        **env_updates,
        "WEB_SEARCH_ENABLED": str(body.web_search_enabled).lower(),
        "RERANK_ENABLED": str(body.rerank_enabled).lower(),
        "RETRIEVAL_TOP_K": str(body.retrieval_top_k),
        "WEB_SEARCH_MAX_RESULTS": str(body.web_search_max_results),
        "CHUNK_SIZE": str(body.chunk_size),
        "CHUNK_OVERLAP": str(body.chunk_overlap),
    })

    # 从更新后的 .env 重新加载配置（保留运行时字段，避免被 .env 默认值覆盖）
    from config import _init_settings
    _runtime_fields = {"qdrant_active_collection", "embedding_dim"}
    _runtime = {f: getattr(settings, f, None) for f in _runtime_fields}
    new_settings = _init_settings()
    for field in settings.model_fields:
        setattr(settings, field, getattr(new_settings, field))
    for f, v in _runtime.items():
        if v:
            setattr(settings, f, v)

    # 检测 embedding 维度变更：用新配置连接 API 获取实际维度，与现有 collection 比对
    dim_info = await _detect_dimension_mismatch()

    # When there are no documents, a dimension mismatch on rag_chunks is
    # harmless — just recreate the empty collection at the new dimension
    # instead of bothering the user with the modal.
    if dim_info.get("mismatch") and dim_info.get("ok"):
        doc_count = dim_info.get("document_count", 0)
        if doc_count == 0:
            try:
                new_dim = dim_info["current_model_dim"]
                await _reset_qdrant_collections(new_dim)
                # Re-check: should now report no mismatch
                dim_info = await _detect_dimension_mismatch()
                logger.info("auto-reset empty collections to dim=%d", new_dim)
            except Exception as e:
                logger.warning("auto-reset empty collections failed: %s", e)

    return {"status": "saved", "dimension": dim_info}


def _env_bool(key: str, default: bool) -> bool:
    """Read boolean from env dict, fallback to config default."""
    val = _read_env().get(key, "").lower()
    if val in ("true", "1", "yes"):
        return True
    if val in ("false", "0", "no"):
        return False
    return default


def _mask_key(key: str) -> str:
    if len(key) <= 8:
        return "***"
    return key[:4] + "***" + key[-4:]


# ── Connection test ──

class TestConnectionRequest(BaseModel):
    provider: str = ""
    model: str = ""
    api_key: str = ""
    base_url: str = ""
    kind: str = "llm"  # "llm" | "embedding"


@router.post("/test-connection")
async def test_connection(req: TestConnectionRequest):
    """Test LLM or embedding connectivity with provided config."""
    import time

    from openai import AsyncOpenAI

    # Treat masked/placeholder keys as empty — fallback to configured keys
    if req.kind == "embedding":
        api_key = (
            req.api_key if (req.api_key and "***" not in req.api_key)
            else (settings.embedding_api_key or settings.llm_api_key)
        )
        base_url = req.base_url or settings.embedding_base_url or settings.llm_base_url
        model = req.model or settings.embedding_model
    else:
        api_key = req.api_key if (req.api_key and "***" not in req.api_key) else settings.llm_api_key
        base_url = req.base_url or settings.llm_base_url
        model = req.model or settings.llm_model

    t0 = time.time()
    http_client = httpx.AsyncClient(proxy=None, trust_env=False)
    client = AsyncOpenAI(api_key=api_key, base_url=base_url, http_client=http_client)

    if req.kind == "embedding":
        try:
            resp = await client.embeddings.create(model=model, input=["hello"])
            latency_ms = int((time.time() - t0) * 1000)
            dim = len(resp.data[0].embedding) if resp.data else 0
            return {"ok": True, "latency_ms": latency_ms, "detail": f"dim={dim}"}
        except Exception as e:
            return {"ok": False, "latency_ms": int((time.time() - t0) * 1000), "detail": str(e)[:300]}
    else:
        try:
            chat_resp = await client.chat.completions.create(
                model=model,
                messages=[{"role": "user", "content": "hi"}],
                max_tokens=5,
            )
            latency_ms = int((time.time() - t0) * 1000)
            reply = (chat_resp.choices[0].message.content or "").strip() if chat_resp.choices else ""
            return {"ok": True, "latency_ms": latency_ms, "detail": reply}
        except Exception as e:
            return {"ok": False, "latency_ms": int((time.time() - t0) * 1000), "detail": str(e)[:300]}


# ── Dimension check & rebuild ──


async def _detect_dimension_mismatch() -> dict:
    """获取当前模型实际维度，与已有 collection 比对。
    返回 {ok, current_model_dim, rag_chunks_dim, profile_dim, mismatch, document_count, error?}
    即使 API 连接失败也返回可用信息（ok=False），不抛异常。
    """
    from openai import AsyncOpenAI
    from sqlalchemy import func, select

    from models.database import session_scope
    from models.orm import Document
    from vectordb.qdrant import QdrantVectorDB

    api_key = settings.embedding_api_key or settings.llm_api_key
    base_url = settings.embedding_base_url or settings.llm_base_url
    model = settings.embedding_model

    # 尝试获取当前模型的实际输出维度
    current_dim = None
    dim_error = None
    try:
        http_client = httpx.AsyncClient(proxy=None, trust_env=False)
        client = AsyncOpenAI(api_key=api_key, base_url=base_url, http_client=http_client)
        resp = await client.embeddings.create(model=model, input=["test"])
        current_dim = len(resp.data[0].embedding) if resp.data else None
    except Exception as e:
        dim_error = str(e)[:300]

    # 获取 collection 维度（使用活跃 collection 名，非默认名 "rag_chunks"）
    import memory.profile as _pf
    chunks_db = QdrantVectorDB()  # 自动使用 settings.qdrant_active_collection or qdrant_collection
    profile_db = QdrantVectorDB(collection_name=_pf.PROFILE_COLLECTION)
    chunks_dim = await chunks_db.get_collection_dim()
    profile_dim = await profile_db.get_collection_dim()

    doc_count = 0
    if chunks_dim is not None:
        async with session_scope() as session:
            result = await session.execute(select(func.count(Document.id)))
            doc_count = result.scalar() or 0

    if current_dim is None:
        return {
            "ok": False,
            "error": f"无法连接 embedding 服务: {dim_error}",
            "rag_chunks_dim": chunks_dim,
            "profile_dim": profile_dim,
            "document_count": doc_count,
        }

    mismatch = (chunks_dim is not None and chunks_dim != current_dim) or \
               (profile_dim is not None and profile_dim != current_dim)

    return {
        "ok": True,
        "current_model_dim": current_dim,
        "rag_chunks_dim": chunks_dim,
        "profile_dim": profile_dim,
        "mismatch": mismatch,
        "document_count": doc_count,
    }


@router.post("/dimension-check")
async def check_dimension():
    """比对当前 embedding 模型实际维度与 Qdrant collection 维度。"""
    return await _detect_dimension_mismatch()


async def _get_actual_embedding_dim() -> int:
    """用当前配置连接 embedding 服务，返回实际向量维度。"""
    from openai import AsyncOpenAI

    from config import settings

    api_key = settings.embedding_api_key or settings.llm_api_key
    base_url = settings.embedding_base_url or settings.llm_base_url
    http_client = httpx.AsyncClient(proxy=None, trust_env=False)
    client = AsyncOpenAI(api_key=api_key, base_url=base_url, http_client=http_client)
    resp = await client.embeddings.create(model=settings.embedding_model, input=["test"])
    return len(resp.data[0].embedding)


async def _reset_qdrant_collections(new_dim: int) -> None:
    """清空所有 Qdrant collection。双缓冲模式：创建新 collection → 切换指针 → 删旧。"""
    import uuid as _uuid_new

    from config import settings
    from vectordb.qdrant import QdrantVectorDB

    # rag_chunks: 创建新空 collection
    new_chunks_name = f"{settings.qdrant_collection}_{_uuid_new.uuid4().hex[:8]}"
    chunks_db_new = QdrantVectorDB(collection_name=new_chunks_name)
    await chunks_db_new.create_collection(new_dim)
    settings.qdrant_active_collection = new_chunks_name

    # user_profile: 创建新空 collection
    import memory.profile as profile_mod
    new_profile_name = f"user_profile_{_uuid_new.uuid4().hex[:8]}"
    profile_db_new = QdrantVectorDB(collection_name=new_profile_name)
    await profile_db_new.create_collection(new_dim)
    profile_mod.PROFILE_COLLECTION = new_profile_name

    # 持久化指针
    import json as _json
    from pathlib import Path
    ptr_file = Path(settings.qdrant_path) / "active_collections.json"
    existing = _json.loads(ptr_file.read_text()) if ptr_file.exists() else {}
    existing["rag_chunks"] = new_chunks_name
    existing["user_profile"] = new_profile_name
    ptr_file.write_text(_json.dumps(existing))

    # 批量清理旧 collection（含裸名 + _* 变体）
    all_collections = await asyncio.to_thread(chunks_db_new.client.get_collections)
    for c in all_collections.collections:
        if (c.name == settings.qdrant_collection
                or c.name.startswith(settings.qdrant_collection + "_")) \
                and c.name != new_chunks_name:
            chunks_db_new.client.delete_collection(c.name)
        if (c.name == "user_profile" or c.name.startswith("user_profile_")) \
                and c.name != new_profile_name:
            chunks_db_new.client.delete_collection(c.name)
        if c.name == "user_memories":
            chunks_db_new.client.delete_collection(c.name)


async def _ensure_profile_collection_dim(target_dim: int) -> None:
    """确保 user_profile collection 存在且维度为 target_dim。
    当画像数据为空时 _index_profile 会提前返回，此时旧 collection（旧维度）残留在磁盘上。
    此函数强制创建正确维度的空 collection 并清理旧数据。
    """
    import json as _json
    import uuid as _uuid
    from pathlib import Path

    import memory.profile as profile_mod
    from vectordb.qdrant import QdrantVectorDB

    # 如果 _index_profile 已经创建了新 collection 且 PROFILE_COLLECTION 指向正确维度，直接返回
    current_coll = profile_mod.PROFILE_COLLECTION
    vdb = QdrantVectorDB(collection_name=current_coll)
    dim = await vdb.get_collection_dim()
    if dim == target_dim:
        return  # already correct

    # 需要强制重建：创建空 collection → 切换指针 → 清理旧 collection
    new_name = f"user_profile_{_uuid.uuid4().hex[:8]}"
    new_vdb = QdrantVectorDB(collection_name=new_name)
    await new_vdb.create_collection(target_dim)
    profile_mod.PROFILE_COLLECTION = new_name

    # 删除所有旧的 user_profile collection（含裸名 + _* 变体）
    all_collections = await asyncio.to_thread(new_vdb.client.get_collections)
    for c in all_collections.collections:
        if (c.name == "user_profile" or c.name.startswith("user_profile_")) and c.name != new_name:
            new_vdb.client.delete_collection(c.name)

    # 持久化指针
    ptr_file = Path(settings.qdrant_path) / "active_collections.json"
    existing = _json.loads(ptr_file.read_text()) if ptr_file.exists() else {}
    existing["user_profile"] = new_name
    ptr_file.write_text(_json.dumps(existing))
    logger.info("profile collection ensured dim=%d name=%s", target_dim, new_name)


# ── Rebuild helpers ──

import re as _re_module


async def _get_sample_text() -> str | None:
    """获取最长的一段文本作为 pre-flight 样本。优先 raw_text，降级 FTS5。"""
    from sqlalchemy import select

    from models.database import session_scope
    from models.orm import Document

    # 1. 尝试从 Document.raw_text 获取
    async with session_scope() as session:
        result = await session.execute(
            select(Document.raw_text)
            .where(Document.raw_text.isnot(None))
            .where(Document.raw_text != "")
            .order_by(Document.file_size.desc())
            .limit(1)
        )
        row = result.first()
        if row and row[0]:
            return row[0]

    # 2. 降级: 从 BM25 回读旧 chunk
    from textdb.bm25_search import BM25Search
    fts = BM25Search()
    try:
        rows = await fts.raw_query(
            "SELECT text FROM bm25_docs ORDER BY length(text) DESC LIMIT 1"
        )
        if rows and rows[0]:
            return _re_module.sub(r'\s+(?=[一-鿿㐀-䶿豈-﫿])', '', rows[0][0]).strip()
    except Exception:
        pass

    return None


async def _preflight_chunk_size(
    sample_text: str,
    initial_size: int,
    embedding,
) -> int:
    """Pre-flight: 从 initial_size 开始试 embed，失败逐级减 1/3 直到成功。"""
    from rag.splitter import split_text

    chunk_size = initial_size
    for _attempt in range(10):
        chunks = split_text(sample_text, chunk_size, settings.chunk_overlap)
        if not chunks:
            return chunk_size
        longest = max(chunks, key=lambda c: len(c.text))
        try:
            await embedding.embed_query(longest.text)
            return chunk_size
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
            if is_length_error:
                new_size = max(chunk_size * 2 // 3, 8)
                logger.warning(
                    "preflight chunk_size too large current=%d next=%d",
                    chunk_size, new_size,
                )
                chunk_size = new_size
            else:
                raise

    raise RuntimeError(f"Pre-flight failed after 10 rounds (final chunk_size={chunk_size})")


async def _upsert_batch(vectordb, points: list[dict], batch_size: int = 50):
    """分批 upsert Qdrant points，避免单次请求过大。"""
    for i in range(0, len(points), batch_size):
        await vectordb.upsert(points[i:i + batch_size])


_rebuild_lock = False
_last_rebuild_result: dict | None = None


async def _scroll_all(client, collection_name: str) -> list:
    """分页 scroll Qdrant collection 的全部 points（含向量）。"""
    all_points = []
    offset = None
    while True:
        batch, next_offset = await asyncio.to_thread(
            client.scroll,
            collection_name=collection_name,
            limit=1000,
            offset=offset,
            with_vectors=True,
            with_payload=True,
        )
        if batch:
            all_points.extend(batch)
        if next_offset is None:
            break
        offset = next_offset
    return all_points


@router.post("/rebuild-collections")
async def rebuild_collections():
    """异步重建向量索引: pre-flight → 双缓冲写入 → 原子切换。"""
    global _rebuild_lock

    if _rebuild_lock:
        return {"status": "rejected", "reason": "重建正在进行中，请等待完成后再试"}

    async def _do_rebuild():
        global _rebuild_lock, _last_rebuild_result
        import uuid as _uuid

        from sqlalchemy import select

        from embedding.factory import create_embedding, reset_embedding
        from memory.profile import _index_profile, _load
        from models.database import session_scope
        from models.orm import DocStatus, Document
        from rag.progress import progress
        from rag.splitter import split_text
        from textdb.bm25_search import BM25Search
        from vectordb.qdrant import QdrantVectorDB

        rebuild_id = "rebuild"

        try:
            # ── 0. 清理上次失败的残留 ──
            _last_rebuild_result = None  # reset stale cache so SSE waits for live events
            progress.publish(rebuild_id, {"status": "preflight", "message": "正在准备工作..."})

            # 重置 embedding 单例，确保使用最新模型配置
            reset_embedding()

            await _cleanup_bm25_v2()

            # ── 1. Pre-flight ──
            progress.publish(rebuild_id, {"status": "preflight", "message": "正在检测模型兼容性..."})

            sample_text = await _get_sample_text()
            embedding = create_embedding()

            if sample_text:
                actual_chunk_size = await _preflight_chunk_size(
                    sample_text, settings.chunk_size, embedding
                )
            else:
                actual_chunk_size = settings.chunk_size

            logger.info("rebuild preflight done chunk_size=%d", actual_chunk_size)

            # ── 2. 逐文档重新处理 ──
            async with session_scope() as session:
                result = await session.execute(
                    select(Document).order_by(Document.created_at.asc())
                )
                docs = result.scalars().all()

            fts_v2 = BM25Search(table_suffix=_V2_SUFFIX)
            # A rebuild with zero documents still needs a complete staged table
            # set so the switch is deterministic. More importantly, never enter
            # the switch phase with an implicitly missing buffer.
            await fts_v2._ensure_tables()

            total_docs = len(docs)
            total_chunks = 0
            doc_chunk_counts: dict[str, int] = {}
            all_points: list[dict] = []  # 内存中收集，避免 Qdrant scroll

            for idx, doc in enumerate(docs):
                progress.publish(rebuild_id, {
                    "status": "rebuilding",
                    "current": idx + 1,
                    "total": total_docs,
                    "filename": doc.filename,
                    "message": f"正在重建: {doc.filename}",
                })

                try:
                    # 获取文本: raw_text 优先，降级 FTS5 回读
                    if doc.raw_text:
                        raw_text = doc.raw_text
                    else:
                        fts_old = BM25Search()
                        rows = await fts_old._query(
                            f"SELECT text FROM bm25_docs WHERE document_id = '{doc.id}' ORDER BY chunk_id"
                        )
                        if not rows:
                            logger.warning("rebuild skip doc_id=%s no raw_text and no fts chunks", doc.id)
                            continue
                        raw_text = "\n".join(
                            _re_module.sub(r'\s+(?=[一-鿿㐀-䶿豈-﫿])', '', row[0]).strip()
                            for row in rows
                        )

                    # 重新切分
                    chunks = split_text(raw_text, actual_chunk_size, settings.chunk_overlap)
                    if not chunks:
                        continue

                    # Embed
                    texts = [c.text for c in chunks]
                    vectors = await embedding.embed(texts)

                    # Build points for Qdrant _v2
                    points = []
                    for i, (chunk, vec) in enumerate(zip(chunks, vectors, strict=False)):
                        chunk_id = str(_uuid.uuid5(_uuid.NAMESPACE_DNS, f"{doc.id}:{i}"))
                        points.append({
                            "id": chunk_id,
                            "vector": vec,
                            "payload": {
                                "document_id": doc.id,
                                "chunk_index": i,
                                "text": chunk.text,
                            },
                        })

                    all_points.extend(points)

                    # 构建 document_key
                    _doc_key = _derive_doc_key(doc.id, doc.filename)

                    # Qdrant payload 补全 document_key/section_key
                    for pt in points:
                        pt["payload"]["document_key"] = _doc_key
                        pt["payload"]["section_key"] = ""

                    # FTS5 _v2 写入
                    fts_entries = [
                        (str(_uuid.uuid5(_uuid.NAMESPACE_DNS, f"{doc.id}:{c.chunk_index}")),
                         doc.id, _doc_key, c.section_key, c.chunk_index, c.text)
                        for c in chunks
                    ]
                    await fts_v2.insert_batch(fts_entries)

                    doc_chunk_counts[doc.id] = len(chunks)
                    total_chunks += len(chunks)

                except Exception as e:
                    logger.error("rebuild doc failed doc_id=%s filename=%s error=%s",
                                 doc.id, doc.filename, str(e)[:200])
                    doc_chunk_counts[doc.id] = 0

            if total_docs > 0 and total_chunks == 0:
                raise RuntimeError(
                    f"所有 {total_docs} 份文档均重建失败，保留现有索引"
                )

            # ── 3. 确定新维度 ──
            new_dim = await _get_actual_embedding_dim()

            # ── 4. 切换 ──
            progress.publish(rebuild_id, {"status": "switching", "message": "正在切换索引..."})

            # 4a. Qdrant rag_chunks: 创建全新 collection → 写入 → 切换指针 → 删旧
            from qdrant_client.models import PointStruct
            new_coll_name = f"{settings.qdrant_collection}_{_uuid.uuid4().hex[:8]}"

            chunks_db_new = QdrantVectorDB(collection_name=new_coll_name)
            await chunks_db_new.create_collection(new_dim)

            if all_points:
                qdrant_points = [
                    PointStruct(id=p["id"], vector=p["vector"], payload=p["payload"])
                    for p in all_points
                ]
                for i in range(0, len(qdrant_points), 50):
                    await asyncio.to_thread(
                        chunks_db_new.client.upsert,
                        collection_name=new_coll_name,
                        points=qdrant_points[i:i + 50],
                    )

            # 原子切换: 更新 active collection 指针 + 持久化
            old_coll_name = settings.qdrant_active_collection or settings.qdrant_collection
            settings.qdrant_active_collection = new_coll_name
            # 持久化 rag_chunks 指针（user_profile 由 _index_profile 内部持久化）
            ptr_file = Path(settings.qdrant_path) / "active_collections.json"
            import json as _json
            existing_ptrs = _json.loads(ptr_file.read_text()) if ptr_file.exists() else {}
            existing_ptrs["rag_chunks"] = new_coll_name
            ptr_file.write_text(_json.dumps(existing_ptrs))

            # 删除旧 collection（含所有同名前缀的旧 collection + 裸名 collection）
            chunks_db_old = QdrantVectorDB(collection_name=old_coll_name)
            if await chunks_db_old.collection_exists():
                chunks_db_old.client.delete_collection(old_coll_name)
            # 清理可能残留的其他旧 collection
            all_collections = await asyncio.to_thread(chunks_db_new.client.get_collections)
            for c in all_collections.collections:
                if (c.name == settings.qdrant_collection
                        or c.name.startswith(settings.qdrant_collection + "_")) \
                        and c.name != new_coll_name:
                    chunks_db_new.client.delete_collection(c.name)

            # 4b. 画像: 双缓冲重建 + 空数据兜底（确保旧 collection 被清理）
            # _index_profile 和 _ensure_profile_collection_dim 分开 try，前者失败不阻断后者
            try:
                profile_data = await _load()
                await _index_profile(profile_data)
            except Exception:
                logger.warning("profile index rebuild failed during switch", exc_info=True)

            try:
                await _ensure_profile_collection_dim(new_dim)
            except Exception:
                logger.warning("profile collection dimension fix failed during switch", exc_info=True)

            # 4c. BM25: drop old tables + rename _v2
            await _switch_bm25_tables()

            # ── 5. 更新 Document 表 ──
            async with session_scope() as session:
                for d in docs:
                    cc = doc_chunk_counts.get(d.id, 0)
                    if cc > 0:
                        d.status = DocStatus.ready
                        d.chunk_count = cc
                        d.embedding_model = settings.embedding_model
                        d.embedding_dim = new_dim
                        d.chunk_size = actual_chunk_size
                        d.error_message = None
                    elif not d.raw_text:
                        d.status = DocStatus.failed
                        d.error_message = "rebuild failed: no raw_text and old chunks exceed model limit, please re-upload"
                    else:
                        d.status = DocStatus.failed
                        d.error_message = "rebuild failed: embedding error, check server logs"
                    session.add(d)
                await session.commit()

            settings.chunk_size = actual_chunk_size
            failed_docs = sum(1 for d in docs if doc_chunk_counts.get(d.id, 0) == 0)

            if total_chunks == 0 and total_docs > 0:
                _last_rebuild_result = {
                    "status": "failed",
                    "error": f"所有 {failed_docs} 份文档均重建失败，知识库未更新",
                }
                progress.publish(rebuild_id, _last_rebuild_result)
            else:
                _last_rebuild_result = {
                    "status": "completed",
                    "chunk_count": total_chunks,
                    "actual_chunk_size": actual_chunk_size,
                    "actual_chunk_dim": new_dim,
                    "failed_count": failed_docs,
                }
                progress.publish(rebuild_id, _last_rebuild_result)

        except Exception as e:
            logger.error("rebuild failed: %s", str(e)[:300], exc_info=True)
            _last_rebuild_result = {"status": "failed", "error": str(e)[:300]}
            # 清理 BM25 _v2 残留
            with contextlib.suppress(Exception):
                await _cleanup_bm25_v2()
            progress.publish(rebuild_id, {
                "status": "failed",
                "error": str(e)[:300],
            })
        finally:
            _rebuild_lock = False

    _rebuild_lock = True
    from worker.tasks import get_task_manager
    try:
        get_task_manager().create(_do_rebuild, "rebuild_collections")
    except Exception:
        _rebuild_lock = False
        raise
    return {"status": "started"}


@router.get("/rebuild-status")
async def rebuild_status():
    """查询最近一次 rebuild 的结果。"""
    if _last_rebuild_result is None:
        return {"status": "idle", "message": "尚未执行过重建"}
    return _last_rebuild_result


@router.post("/clear-all-data")
async def clear_all_data():
    """清空所有数据（文档/切片/向量/记忆/对话/画像），重建空 collection。"""
    from sqlalchemy import delete, func, select

    from config import settings
    from models.database import session_scope, engine
    from models.orm import Conversation, Document, Message, UserMemory, UserProfile

    new_dim = await _get_actual_embedding_dim()

    # 统计待删除数量
    async with session_scope() as session:
        doc_count = (await session.execute(select(func.count(Document.id)))).scalar() or 0
        mem_count = (await session.execute(select(func.count(UserMemory.id)))).scalar() or 0
        conv_count = (await session.execute(select(func.count(Conversation.id)))).scalar() or 0
        msg_count = (await session.execute(select(func.count(Message.id)))).scalar() or 0

    # 删除 Qdrant collection + 按新维度重建（含 user_memories 残留清理）
    await _reset_qdrant_collections(new_dim)

    # 清空 SQLite 表
    async with session_scope() as session:
        await session.execute(delete(Message))
        await session.execute(delete(Conversation))
        await session.execute(delete(UserMemory))
        await session.execute(delete(UserProfile))
        await session.execute(delete(Document))
        await session.commit()

    # 清空 BM25 + FTS5
    async with engine.begin() as conn:
        from sqlalchemy import text as sa_text
        for t in ("bm25_docs", "bm25_index", "bm25_stats", "chunks_fts"):
            await conn.execute(sa_text(f"DELETE FROM {t}"))

    # 清空上传文件
    # TODO(D3): use get_storage() to enumerate and delete all stored objects
    # once documents consistently use storage_key instead of flat filenames.
    upload_dir = settings.upload_dir
    if os.path.isdir(upload_dir):
        for f in os.listdir(upload_dir):
            fp = os.path.join(upload_dir, f)
            if os.path.isfile(fp):
                os.remove(fp)

    return {
        "status": "cleared",
        "deleted": {
            "documents": doc_count,
            "chunks": doc_count,  # 近似值，实际由 FTS5 行数决定
            "memories": mem_count,
            "conversations": conv_count,
            "messages": msg_count,
        },
    }


# ── Rebuild progress SSE ──

@router.get("/rebuild-progress")
async def rebuild_progress():
    """SSE 端点，监听 rebuild 全局进度。"""
    from rag.progress import progress

    async def event_stream():
        # Send an immediate comment so the browser knows the SSE connection
        # is alive.  Some browsers/proxies close EventSource connections
        # that produce no data within the first few seconds.
        yield ":ok\n\n"

        # Subscribe first to avoid the race where rebuild completes between
        # the _rebuild_lock check and the subscription call.
        try:
            q = await progress.subscribe("rebuild")
        except Exception:
            logger.error("SSE rebuild-progress subscribe failed", exc_info=True)
            yield f"data: {{\"status\": \"failed\", \"error\": \"内部错误: 订阅失败\"}}\n\n"
            return
        try:
            # Only send cached result when rebuild is truly done.  If a new
            # rebuild is in-flight (_rebuild_lock=True), wait for live events
            # even if a stale _last_rebuild_result from a prior run is present.
            if _last_rebuild_result is not None and not _rebuild_lock:
                yield f"data: {json.dumps(_last_rebuild_result, ensure_ascii=False)}\n\n"
                return
            while True:
                try:
                    event = await asyncio.wait_for(q.get(), timeout=300)
                    yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
                    if event.get("status") in ("completed", "failed"):
                        break
                except TimeoutError:
                    yield "data: {\"status\": \"timeout\"}\n\n"
                    break
        finally:
            progress.unsubscribe("rebuild", q)

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
