import asyncio
import contextlib
import json
import logging
import re as _re_module
from pathlib import Path

import httpx
from fastapi import APIRouter
from fastapi.responses import StreamingResponse

from api.settings_connection import test_connection
from api.settings_env import env_bool, mask_key, read_env, write_env
from api.settings_models import (
    EmbeddingSettings,
    LLMSettings,
    SettingsResponse,
    TestConnectionRequest,
)
from api.settings_rebuild import (
    activate_qdrant_and_bm25 as _activate_qdrant_and_bm25,
)
from api.settings_rebuild import (
    cleanup_bm25_v2 as _cleanup_bm25_v2,
)
from api.settings_rebuild import (
    derive_doc_key as _derive_doc_key,
)
from api.settings_rebuild import (
    get_sample_text as _get_sample_text,
)
from api.settings_rebuild import (
    preflight_chunk_size as _preflight_chunk_size,
)
from api.settings_rebuild import (
    switch_bm25_tables as _switch_bm25_tables,
)
from config import settings

__all__ = [
    "EmbeddingSettings",
    "LLMSettings",
    "SettingsResponse",
    "TestConnectionRequest",
    "router",
    "test_connection",
]

router = APIRouter(prefix="/api/settings", tags=["settings"])
_V2_SUFFIX = "_v2"

logger = logging.getLogger(__name__)

ENV_PATH = Path(__file__).resolve().parent.parent / ".env"


def _read_env() -> dict[str, str]:
    """Read .env file into a dict."""
    return read_env(ENV_PATH)


def _write_env(env: dict[str, str]) -> None:
    """Write dict to .env file, preserving existing non-related keys."""
    write_env(ENV_PATH, env)


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
    return env_bool(_read_env(), key, default)


def _mask_key(key: str) -> str:
    return mask_key(key)


# Register the extracted implementation while keeping api.settings imports stable.
router.post("/test-connection")(test_connection)


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
    timeout = httpx.Timeout(connect=settings.llm_connect_timeout, read=10.0)
    http_client = httpx.AsyncClient(proxy=None, trust_env=False, timeout=timeout)
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


_rebuild_lock = False
_last_rebuild_result: dict | None = None


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

            # Prepare the old pointer, but keep it active until the BM25 switch
            # succeeds.  The activation helper restores it on any failure.
            old_coll_name = settings.qdrant_active_collection or settings.qdrant_collection
            ptr_file = Path(settings.qdrant_path) / "active_collections.json"

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

            # 4c. Activate Qdrant + BM25 as one compensated switch.  On a BM25
            # failure, the old vector pointer/collection remain available.
            await _activate_qdrant_and_bm25(
                chunks_db_new,
                new_coll_name,
                old_coll_name,
                ptr_file,
                _switch_bm25_tables,
            )

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
    # Publish an initial event immediately so /rebuild-status returns
    # useful progress data before the background task has started.
    from rag.progress import progress as _rebuild_progress
    _rebuild_progress.publish("rebuild", {"status": "preflight", "message": "正在启动重建..."})

    from worker.tasks import get_task_manager
    try:
        get_task_manager().create(_do_rebuild, "rebuild_collections")
    except Exception:
        _rebuild_lock = False
        raise
    return {"status": "started"}


@router.get("/rebuild-status")
async def rebuild_status():
    """查询最近一次 rebuild 的结果或当前重建的进度。

    优先返回最终结果（completed/failed），其次是实时进度事件，
    最后返回 idle 状态。
    """
    from rag.progress import progress

    # A rebuild is running: return the latest live progress event so
    # polling-based UIs can show document-level detail.
    if _rebuild_lock:
        latest = progress._latest.get("rebuild")
        if latest is not None:
            return latest
        return {"status": "rebuilding", "message": "重建进行中..."}

    if _last_rebuild_result is not None:
        return _last_rebuild_result

    return {"status": "idle", "message": "尚未执行过重建"}


@router.post("/clear-all-data")
async def clear_all_data():
    """清空所有数据（文档/切片/向量/记忆/对话/画像），重建空 collection。"""
    from sqlalchemy import delete, func, select

    from models.database import engine, session_scope
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

    # 清空后端管理的对象、暂存数据及兼容期旧文件。
    from storage import get_storage

    await get_storage().clear()

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
            yield "data: {\"status\": \"failed\", \"error\": \"内部错误: 订阅失败\"}\n\n"
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
