"""用户画像管理器 — 画像为唯一真相源，直接读写，embedding 语义去重 + Qdrant 索引。"""

import asyncio
import logging
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from functools import wraps
from typing import Any

from memory.profile_core import (
    MAX_FACTS,
)
from memory.profile_core import (
    empty_profile as _empty,
)
from memory.profile_core import (
    evict_facts as _evict_facts,
)
from memory.profile_core import (
    flatten_profile as _flatten,
)
from memory.profile_core import (
    format_profile_text as format_profile,
)
from memory.profile_core import (
    parse_item_id as _parse_id,
)
from memory.profile_core import (
    score_fact as _score_fact,
)
from memory.profile_store import load_profile, save_profile

__all__ = [
    "MAX_FACTS",
    "_empty",
    "_evict_facts",
    "_flatten",
    "_parse_id",
    "_score_fact",
    "format_profile",
]

logger = logging.getLogger(__name__)

class _AsyncReentrantLock:
    """Task-reentrant lock for nested profile mutation helpers."""

    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self._owner: asyncio.Task[Any] | None = None
        self._depth = 0

    async def __aenter__(self) -> None:
        task = asyncio.current_task()
        if task is not None and task is self._owner:
            self._depth += 1
            return
        await self._lock.acquire()
        self._owner = task
        self._depth = 1

    async def __aexit__(self, *_exc: object) -> None:
        self._depth -= 1
        if self._depth == 0:
            self._owner = None
            self._lock.release()


_PROFILE_WRITE_LOCK = _AsyncReentrantLock()


def _serialized_write[**P, R](
    fn: Callable[P, Awaitable[R]],
) -> Callable[P, Awaitable[R]]:
    @wraps(fn)
    async def wrapped(*args: P.args, **kwargs: P.kwargs) -> R:
        async with _PROFILE_WRITE_LOCK:
            return await fn(*args, **kwargs)

    return wrapped

PROFILE_COLLECTION = "user_profile"
PROFILE_INDEX_DIRTY = False


async def _load() -> dict:
    return await load_profile()


@_serialized_write
async def _save(data: dict):
    await save_profile(data)
    # 写入 Qdrant 索引
    await _index_profile(data)


# ── 语义去重 ──────────────────────────

async def _is_similar(text: str, candidates: list[str]) -> bool:
    """用 embedding 余弦相似度判断是否重复。含字符串查重兜底。"""
    if not candidates:
        return False

    # Fast path: exact match avoids embedding API cost
    if text in candidates:
        return True

    from embedding.factory import create_embedding
    emb = create_embedding()
    try:
        vec = await emb.embed_query(text)
        cand_vecs = await emb.embed(candidates)
        from numpy import dot
        from numpy.linalg import norm
        for cv in cand_vecs:
            sim = dot(vec, cv) / (norm(vec) * norm(cv))
            if sim >= 0.85:
                return True
    except Exception:
        # Embedding API failed — fall back to string heuristics so we
        # don't silently store duplicates when the API is unreachable.
        for c in candidates:
            if not c:
                continue
            # Substring containment (safe for API-failure fallback)
            if text in c or c in text:
                return True
            # Character-level Jaccard similarity
            text_chars = set(text)
            cand_chars = set(c)
            overlap = len(text_chars & cand_chars) / max(len(text_chars | cand_chars), 1)
            if overlap >= 0.7:
                return True
    return False


async def _cleanup_old_profile_collections() -> bool:
    """删除所有 user_profile_* 前缀的 collection，用于画像清空后清理残留。"""
    try:
        from vectordb.qdrant import QdrantVectorDB
        vdb = QdrantVectorDB(collection_name="user_profile_dummy")
        all_collections = await asyncio.to_thread(vdb.client.get_collections)
        for c in all_collections.collections:
            if c.name == "user_profile" or c.name.startswith("user_profile_"):
                vdb.client.delete_collection(c.name)
        return True
    except Exception:
        return False


# ── Qdrant 索引 ───────────────────────

async def _index_profile(data: dict):
    """将画像关键字段索引到 Qdrant，供 recall_memory 语义搜索。

    使用双缓冲模式：创建新 collection → 写入 → 切换指针 → 删旧。
    避免 Qdrant 本地模式的 delete+create 数据腐败问题。
    """
    global PROFILE_COLLECTION, PROFILE_INDEX_DIRTY
    PROFILE_INDEX_DIRTY = True
    try:
        from embedding.factory import create_embedding
        from vectordb.qdrant import QdrantVectorDB

        # 展开为可搜索的文本片段
        texts: list[str] = []
        if data.get("name"):
            texts.append(f"用户名叫{data['name']}")
        if data.get("role"):
            texts.append(f"用户是{data['role']}")
        for p in data.get("preferences", []):
            texts.append(p)
        for d in data.get("decisions", []):
            texts.append(d)
        for f in data.get("facts", []):
            c = f["content"] if isinstance(f, dict) else f
            texts.append(c)

        if not texts:
            # 画像为空时仍需确保旧 collection 被清理，避免残留旧维度数据
            if await _cleanup_old_profile_collections():
                PROFILE_INDEX_DIRTY = False
            return

        emb = create_embedding()
        vectors = await emb.embed(texts)

        # 创建全新 collection，避免 delete+create 腐败
        import uuid as _uuid
        new_name = f"user_profile_{_uuid.uuid4().hex[:8]}"

        vdb = QdrantVectorDB(collection_name=new_name)
        await vdb.create_collection(len(vectors[0]))

        points = []
        for i, (text, vec) in enumerate(zip(texts, vectors, strict=False)):
            point_id = str(_uuid.uuid5(_uuid.NAMESPACE_DNS, f"user_profile_{i}"))
            points.append({
                "id": point_id,
                "vector": vec,
                "payload": {"text": text, "index": i},
            })
        await vdb.upsert(points)

        # 原子切换指针
        PROFILE_COLLECTION = new_name

        # 删除旧的 + 同名前缀残留（含裸名 + _* 变体）
        all_collections = await asyncio.to_thread(vdb.client.get_collections)
        for c in all_collections.collections:
            if (c.name == "user_profile" or c.name.startswith("user_profile_")) and c.name != new_name:
                vdb.client.delete_collection(c.name)

        # 持久化指针
        _persist_profile_collection(new_name)
        PROFILE_INDEX_DIRTY = False

    except Exception:
        logger.warning("profile Qdrant index failed, recall_memory may return stale results", exc_info=True)


def _persist_profile_collection(name: str):
    """将 profile collection 名持久化，重启后恢复。"""
    try:
        import json as _json
        from pathlib import Path

        from config import settings
        ptr_file = Path(settings.qdrant_path) / "active_collections.json"
        existing = {}
        if ptr_file.exists():
            existing = _json.loads(ptr_file.read_text())
        existing["user_profile"] = name
        ptr_file.write_text(_json.dumps(existing))
    except Exception:
        pass


# ── 字段更新 ──────────────────────────

@_serialized_write
async def upsert_field(field: str, value: str) -> dict:
    data = await _load()
    data[field] = value
    await _save(data)
    return data


@_serialized_write
async def append_list(field: str, value: str) -> dict:
    data = await _load()
    items = data.get(field, [])
    if not await _is_similar(value, items):
        items.append(value)
        if len(items) > MAX_FACTS:
            items = items[-MAX_FACTS:]
        data[field] = items
        await _save(data)
    return data


@_serialized_write
async def append_facts(items: list[str], source: str = "session") -> dict:
    data = await _load()
    existing = [f["content"] if isinstance(f, dict) else f for f in data.get("facts", [])]
    new_count = 0
    for item in items:
        if not await _is_similar(item, existing):
            data.setdefault("facts", []).append({
                "content": item, "source": source,
                "ts": datetime.now(UTC).isoformat(),
                "access_count": 0,
            })
            existing.append(item)
            new_count += 1
    data["facts"] = _evict_facts(data["facts"], MAX_FACTS)
    if new_count > 0:
        logger.info("memory facts appended total=%d new=%d", len(data["facts"]), new_count)
    await _save(data)
    return data


# ── 身份字段跨字段去重 ────────────────

async def _identity_similar(value: str, data: dict) -> bool:
    """Check if value is semantically similar to existing name or role."""
    existing = []
    if data.get("name"):
        existing.append(data["name"])
    if data.get("role"):
        existing.append(data["role"])
    return await _is_similar(value, existing) if existing else False


# ── 拦截器入口 ────────────────────────

@_serialized_write
async def handle_intercept(content: str, mem_type: str) -> dict:
    if mem_type == "identity_name":
        data = await _load()
        if content and not await _identity_similar(content, data):
            return await upsert_field("name", content)
        return data

    if mem_type == "identity_role":
        data = await _load()
        if content and not await _identity_similar(content, data):
            return await upsert_field("role", content)
        return data

    if mem_type == "identity":
        data = await _load()
        # Try name patterns first
        if "用户叫" in content or "的名字" in content or "姓名" in content:
            value = content
            for prefix in ("用户叫", "用户的名字是", "用户的名字叫", "用户的姓名是", "姓名是"):
                if prefix in value:
                    value = value.replace(prefix, "").strip()
                    break
            if value and not await _identity_similar(value, data):
                return await upsert_field("name", value)
            return data
        # Then role/identity patterns
        if "用户是" in content:
            value = content.replace("用户是", "").strip()
            if value and not await _identity_similar(value, data):
                return await upsert_field("role", value)
            return data
        for prefix in ("用户的职业是", "用户的身份是", "用户的职位是", "用户的角色是",
                       "用户的职责是", "用户工作是"):
            if prefix in content:
                value = content.replace(prefix, "").strip()
                if value and not await _identity_similar(value, data):
                    return await upsert_field("role", value)
                return data
        # Fallback: treat as role only if it looks like a statement about the user
        if not await _identity_similar(content, data):
            return await upsert_field("role", content)
        return data
    elif mem_type == "preference":
        return await append_list("preferences", content)
    elif mem_type == "decision":
        return await append_list("decisions", content)
    elif mem_type == "fact":
        return await append_facts([content], source="interceptor")
    return await _load()


# ── 会话提取入口 ──────────────────────

@_serialized_write
async def handle_session_extract(extracted: list[dict]) -> dict:
    data = await _load()
    new_count = 0
    for item in extracted:
        content = item.get("content", "")
        mem_type = item.get("memory_type", "fact")
        if not content:
            continue
        if mem_type == "identity_name":
            if content and not await _identity_similar(content, data):
                data["name"] = content
                new_count += 1
        elif mem_type == "identity_role":
            if content and not await _identity_similar(content, data):
                data["role"] = content
                new_count += 1
        elif mem_type == "identity":
            # Name patterns: explicit name markers or "名字是" phrasing
            if "叫" in content or "名字" in content or "姓名" in content:
                value = content
                for sep in ("叫", "名字是", "名字叫", "姓名是"):
                    if sep in value:
                        value = value.split(sep, 1)[-1].strip()
                        break
                if value and not await _identity_similar(value, data):
                    data["name"] = value
                    new_count += 1
            # Role patterns: "职业/身份/职责/工作是" or generic "是"
            elif "是" in content:
                value = content.split("是", 1)[-1].strip()
                if value and not await _identity_similar(value, data):
                    data["role"] = value
                    new_count += 1
            elif not await _identity_similar(content, data):
                data["role"] = content
                new_count += 1
        elif mem_type == "preference":
            items = data.setdefault("preferences", [])
            if not await _is_similar(content, items):
                items.append(content)
                new_count += 1
        elif mem_type == "decision":
            items = data.setdefault("decisions", [])
            if not await _is_similar(content, items):
                items.append(content)
                new_count += 1
        else:
            texts = [f["content"] if isinstance(f, dict) else f for f in data.get("facts", [])]
            if not await _is_similar(content, texts):
                data.setdefault("facts", []).append({
                    "content": content, "source": "session",
                    "ts": datetime.now(UTC).isoformat(),
                    "access_count": 0,
                })
                new_count += 1
    data["facts"] = _evict_facts(data.get("facts", []), MAX_FACTS)
    if new_count > 0:
        logger.info("memory session extracted total=%d new=%d", len(extracted), new_count)
    await _save(data)
    return data


# ── 语义搜索（recall_memory 工具用）───

def _embedding_configured() -> bool:
    """Check if any embedding API key is available."""
    from config import settings
    return bool(settings.embedding_api_key or settings.llm_api_key)


async def rebuild_index():
    """强制重建画像 Qdrant 索引。启动时调用。

    当画像为空或 embedding key 未配置时跳过语义索引。
    """
    if not _embedding_configured():
        logger.info("memory index skipped — no embedding API key configured")
        return
    data = await _load()
    await _index_profile(data)
    logger.info("memory index rebuilt vectors=%d", len(_flatten(data)))


async def search_profile(query: str, top_k: int = 5) -> list[dict]:
    """从画像索引中语义搜索，结果为空时兜底用画像直匹配。"""
    # 1. Qdrant 语义搜索
    try:
        from embedding.factory import create_embedding
        from vectordb.qdrant import QdrantVectorDB

        vdb = QdrantVectorDB(collection_name=PROFILE_COLLECTION)
        if not PROFILE_INDEX_DIRTY and await vdb.collection_exists():
            emb = create_embedding()
            vec = await emb.embed_query(query)
            results = await vdb.search(vec, top_k=top_k)
            if results:
                return [
                    {"text": r.text, "score": round(r.score, 4)}
                    for r in results
                ]
    except Exception:
        logger.warning("profile Qdrant search failed, falling back to direct match", exc_info=True)

    # 2. 兜底 — 直接从画像字段匹配
    data = await _load()
    texts = _flatten(data)
    if not texts:
        return []

    from embedding.factory import create_embedding
    emb = create_embedding()
    try:
        q_vec = await emb.embed_query(query)
        doc_vecs = await emb.embed(texts)
        from numpy import dot
        from numpy.linalg import norm
        scored = []
        for text, dv in zip(texts, doc_vecs, strict=False):
            sim = float(dot(q_vec, dv) / (norm(q_vec) * norm(dv)))
            scored.append((text, sim))
        scored.sort(key=lambda x: x[1], reverse=True)
        return [
            {"text": t, "score": round(s, 4)}
            for t, s in scored[:top_k]
        ]
    except Exception:
        return []


# ── 单条增删改 ──────────────────────


@_serialized_write
async def update_item(item_id: str, content: str) -> dict | None:
    """更新画像中的单条记录。返回更新后的画像。"""
    parsed = _parse_id(item_id)
    if parsed is None:
        return None
    field, index = parsed

    data = await _load()
    if field in ("name", "role"):
        data[field] = content
    elif field == "facts":
        items = data.get("facts", [])
        if not (0 <= index < len(items)):
            return None
        if isinstance(items[index], dict):
            items[index]["content"] = content
            items[index]["ts"] = datetime.now(UTC).isoformat()
        else:
            items[index] = content
    else:
        items = data.get(field, [])
        if not (0 <= index < len(items)):
            return None
        items[index] = content

    await _save(data)
    return data


@_serialized_write
async def delete_item(item_id: str) -> dict | None:
    """删除画像中的单条记录。返回更新后的画像或 None。"""
    parsed = _parse_id(item_id)
    if parsed is None:
        return None
    field, index = parsed

    data = await _load()
    if field in ("name", "role"):
        data[field] = ""
    elif field == "facts":
        items = data.get("facts", [])
        if not (0 <= index < len(items)):
            return None
        items.pop(index)
    else:
        items = data.get(field, [])
        if not (0 <= index < len(items)):
            return None
        items.pop(index)

    await _save(data)
    return data


# ── 读取 ──────────────────────────────

async def get_profile() -> dict:
    return await _load()
