"""用户画像管理器 — 画像为唯一真相源，直接读写，embedding 语义去重 + Qdrant 索引。"""

import asyncio
import logging
from datetime import datetime, timezone
from sqlalchemy import select
from models.database import async_session
from models.orm import UserProfile

logger = logging.getLogger(__name__)

MAX_FACTS = 30
PROFILE_COLLECTION = "user_profile"


def _empty() -> dict:
    return {"name": "", "role": "", "preferences": [], "decisions": [], "facts": []}


async def _load() -> dict:
    async with async_session() as s:
        r = await s.execute(
            select(UserProfile).order_by(UserProfile.version.desc()).limit(1)
        )
        row = r.scalar_one_or_none()
        return row.profile_data if row else _empty()


async def _save(data: dict):
    async with async_session() as s:
        r = await s.execute(
            select(UserProfile).order_by(UserProfile.version.desc()).limit(1)
        )
        row = r.scalar_one_or_none()
        if row:
            row.profile_data = data
            row.version += 1
            row.generated_at = datetime.now(timezone.utc)
        else:
            s.add(UserProfile(profile_data=data, memory_ids=[], version=1))
        await s.commit()
    # 写入 Qdrant 索引
    await _index_profile(data)


# ── 语义去重 ──────────────────────────

async def _is_similar(text: str, candidates: list[str]) -> bool:
    """用 embedding 余弦相似度判断是否重复。"""
    if not candidates:
        return False
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
        return False
    return False


async def _cleanup_old_profile_collections() -> None:
    """删除所有 user_profile_* 前缀的 collection，用于画像清空后清理残留。"""
    try:
        from vectordb.qdrant import QdrantVectorDB
        vdb = QdrantVectorDB(collection_name="user_profile_dummy")
        all_collections = await asyncio.to_thread(vdb.client.get_collections)
        for c in all_collections.collections:
            if c.name == "user_profile" or c.name.startswith("user_profile_"):
                vdb.client.delete_collection(c.name)
    except Exception:
        pass


# ── Qdrant 索引 ───────────────────────

async def _index_profile(data: dict):
    """将画像关键字段索引到 Qdrant，供 recall_memory 语义搜索。

    使用双缓冲模式：创建新 collection → 写入 → 切换指针 → 删旧。
    避免 Qdrant 本地模式的 delete+create 数据腐败问题。
    """
    global PROFILE_COLLECTION
    try:
        from vectordb.qdrant import QdrantVectorDB
        from embedding.factory import create_embedding

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
            await _cleanup_old_profile_collections()
            return

        emb = create_embedding()
        vectors = await emb.embed(texts)

        # 创建全新 collection，避免 delete+create 腐败
        import uuid as _uuid
        new_name = f"user_profile_{_uuid.uuid4().hex[:8]}"

        vdb = QdrantVectorDB(collection_name=new_name)
        await vdb.create_collection(len(vectors[0]))

        points = []
        for i, (text, vec) in enumerate(zip(texts, vectors)):
            point_id = str(_uuid.uuid5(_uuid.NAMESPACE_DNS, f"user_profile_{i}"))
            points.append({
                "id": point_id,
                "vector": vec,
                "payload": {"text": text, "index": i},
            })
        await vdb.upsert(points)

        # 原子切换指针
        old_name = PROFILE_COLLECTION
        PROFILE_COLLECTION = new_name

        # 删除旧的 + 同名前缀残留（含裸名 + _* 变体）
        all_collections = await asyncio.to_thread(vdb.client.get_collections)
        for c in all_collections.collections:
            if (c.name == "user_profile" or c.name.startswith("user_profile_")) and c.name != new_name:
                vdb.client.delete_collection(c.name)

        # 持久化指针
        _persist_profile_collection(new_name)

    except Exception:
        logger.warning("profile Qdrant index failed, recall_memory may return stale results", exc_info=True)


def _persist_profile_collection(name: str):
    """将 profile collection 名持久化，重启后恢复。"""
    try:
        from config import settings
        from pathlib import Path
        import json as _json
        ptr_file = Path(settings.qdrant_path) / "active_collections.json"
        existing = {}
        if ptr_file.exists():
            existing = _json.loads(ptr_file.read_text())
        existing["user_profile"] = name
        ptr_file.write_text(_json.dumps(existing))
    except Exception:
        pass


# ── 字段更新 ──────────────────────────

async def upsert_field(field: str, value: str) -> dict:
    data = await _load()
    data[field] = value
    await _save(data)
    return data


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


async def append_facts(items: list[str], source: str = "session") -> dict:
    data = await _load()
    existing = [f["content"] if isinstance(f, dict) else f for f in data.get("facts", [])]
    new_count = 0
    for item in items:
        if not await _is_similar(item, existing):
            data.setdefault("facts", []).append({
                "content": item, "source": source,
                "ts": datetime.now(timezone.utc).isoformat(),
            })
            existing.append(item)
            new_count += 1
    if len(data["facts"]) > MAX_FACTS:
        data["facts"] = data["facts"][-MAX_FACTS:]
    if new_count > 0:
        logger.info("memory facts appended total=%d new=%d", len(data["facts"]), new_count)
    await _save(data)
    return data


# ── 拦截器入口 ────────────────────────

async def handle_intercept(content: str, mem_type: str) -> dict:
    if mem_type == "identity":
        if "用户叫" in content:
            return await upsert_field("name", content.replace("用户叫", "").strip())
        elif "用户是" in content:
            return await upsert_field("role", content.replace("用户是", "").strip())
        else:
            return await upsert_field("role", content)
    elif mem_type == "preference":
        return await append_list("preferences", content)
    elif mem_type == "decision":
        return await append_list("decisions", content)
    elif mem_type == "fact":
        return await append_facts([content], source="interceptor")
    return await _load()


# ── 会话提取入口 ──────────────────────

async def handle_session_extract(extracted: list[dict]) -> dict:
    data = await _load()
    new_count = 0
    for item in extracted:
        content = item.get("content", "")
        mem_type = item.get("memory_type", "fact")
        if not content:
            continue
        if mem_type == "identity":
            if "是" in content:
                data["role"] = content.split("是", 1)[-1].strip()
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
                    "ts": datetime.now(timezone.utc).isoformat(),
                })
                new_count += 1
    if len(data.get("facts", [])) > MAX_FACTS:
        data["facts"] = data["facts"][-MAX_FACTS:]
    if new_count > 0:
        logger.info("memory session extracted total=%d new=%d", len(extracted), new_count)
    await _save(data)
    return data


# ── 语义搜索（recall_memory 工具用）───

async def rebuild_index():
    """强制重建画像 Qdrant 索引。启动时调用。"""
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
        if await vdb.collection_exists():
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
        for text, dv in zip(texts, doc_vecs):
            sim = float(dot(q_vec, dv) / (norm(q_vec) * norm(dv)))
            scored.append((text, sim))
        scored.sort(key=lambda x: x[1], reverse=True)
        return [
            {"text": t, "score": round(s, 4)}
            for t, s in scored[:top_k]
        ]
    except Exception:
        return []


def _flatten(data: dict) -> list[str]:
    """展开画像为可搜索的文本列表。"""
    texts = []
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
    return texts


# ── 单条增删改 ──────────────────────

_FIELD_MAP = {"name": "name", "role": "role", "preference": "preferences",
              "decision": "decisions", "fact": "facts"}


def _parse_id(item_id: str) -> tuple[str, int] | None:
    """解析复合 ID 'field_tag:index'。"""
    parts = item_id.split(":", 1)
    if len(parts) != 2:
        return None
    field = _FIELD_MAP.get(parts[0])
    if field is None:
        return None
    try:
        index = int(parts[1])
    except ValueError:
        return None
    return field, index


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
            items[index]["ts"] = datetime.now(timezone.utc).isoformat()
        else:
            items[index] = content
    else:
        items = data.get(field, [])
        if not (0 <= index < len(items)):
            return None
        items[index] = content

    await _save(data)
    return data


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


def format_profile(profile: dict) -> str:
    if not profile:
        return ""
    parts = []
    if profile.get("name"):
        parts.append(f"用户名: {profile['name']}")
    if profile.get("role"):
        parts.append(f"职业: {profile['role']}")
    prefs = profile.get("preferences", [])
    if prefs:
        parts.append(f"偏好: {'、'.join(prefs)}")
    decs = profile.get("decisions", [])
    if decs:
        parts.append(f"已知决策: {'、'.join(decs)}")
    facts = profile.get("facts", [])
    if facts:
        flat = [f["content"] if isinstance(f, dict) else f for f in facts]
        parts.append(f"补充信息: {'、'.join(flat[-10:])}")
    if not parts:
        return ""
    return "## 用户画像\n" + "\n".join(parts) + "\n"
