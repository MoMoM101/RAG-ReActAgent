# Phase 1.1 — UserProfile 存储优化设计

**日期**: 2026-07-01 | **优先级**: 高 | **预计改动范围**: 1 文件

---

## 问题描述

`backend/memory/profile.py` 的 `_save()` 方法每次调用执行 `INSERT`，新增一行 UserProfile 记录。用户每透露一次个人信息（"我叫XX"、"我喜欢YY"），就会产生一条 ~1KB 的新记录，且旧记录不会被清理。

当前代码 (`profile.py:25-33`):

```python
async def _save(data: dict):
    async with async_session() as s:
        r = await s.execute(
            select(UserProfile).order_by(UserProfile.version.desc()).limit(1)
        )
        row = r.scalar_one_or_none()
        v = (row.version + 1) if row else 1
        s.add(UserProfile(profile_data=data, memory_ids=[], version=v))
        await s.commit()
    await _index_profile(data)
```

问题：
- `s.add()` → INSERT，不是 UPDATE
- 旧版本从未被清理，数据库无限增长
- 每次写入后触发 `_index_profile()`，重建整个 Qdrant 索引（全量 embedding）

---

## 设计方案

### 核心改动：INSERT 改为 UPDATE

将 `_save()` 改为更新已有记录，不再追加新行。版本号递增但只保留最新一条记录。

```python
async def _save(data: dict):
    async with async_session() as s:
        r = await s.execute(
            select(UserProfile).order_by(UserProfile.version.desc()).limit(1)
        )
        row = r.scalar_one_or_none()
        if row:
            row.profile_data = data
            row.version += 1
            row.updated_at = datetime.now(timezone.utc)
        else:
            v = 1
            s.add(UserProfile(profile_data=data, memory_ids=[], version=v))
        await s.commit()
    await _index_profile(data)
```

### 同时优化的子问题：Qdrant 索引重建频率

当前每次 `_save()` 后都调 `_index_profile()`，后者会：
1. 展开 profile 所有字段为文本列表
2. 对**所有文本**重新做 embedding
3. 用 upsert 覆盖 Qdrant 索引

优化：改为**增量更新**——只对新字段做 embedding，追加到 Qdrant 而非全量重建。但考虑到 profile 字段数量极少（name/role/preferences/decisions/facts，通常 < 50 条），全量重建成本很低，此优化可延后到后续分期。

### 不改的部分

- `MAX_FACTS = 30` 保留，facts 超限时截断尾部
- `_is_similar()` embedding 去重逻辑保留
- 版本号字段保留（向前兼容，且为未来可能的版本历史功能留扩展点）
- `memory_ids` 字段保留（虽当前未使用，可能有前端或其他模块引用）

---

## 影响范围

| 文件 | 改动 |
|------|------|
| `backend/memory/profile.py` | `_save()` 函数：INSERT → UPDATE |

无 API 变更，无前端变更，无数据库 schema 变更。

---

## 验证方式

1. 启动后端，发送一条包含个人信息（"我叫测试用户"）的聊天消息
2. 检查数据库中 UserProfile 表：应只有 1 行，version 可能 >1
3. 再次发送另一条个人信息（"我喜欢Python"）
4. 检查数据库：仍只有 1 行，profile_data 包含两条信息
5. 运行现有测试：`pytest backend/tests/ -v`
