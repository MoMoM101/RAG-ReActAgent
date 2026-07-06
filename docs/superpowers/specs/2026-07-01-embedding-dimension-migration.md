# 向量模型切换 — 维度校验与自动重建设计

**日期**: 2026-07-01 | **改动范围**: 后端 2 文件 + 前端 2 文件

---

## 问题

用户更换 embedding 模型后，Qdrant collection 维度不匹配。旧数据与新模型不兼容。

---

## 数据分布

| 数据 | 存储位置 | 删 Qdrant 后 |
|------|----------|--------------|
| 原始文件 | `./data/uploads/` | 在 |
| 文档元数据 | SQLite `documents` 表 | 在 |
| 切片文本 | SQLite FTS5 `chunks_fts` | 在 |
| 切片向量 | Qdrant `rag_chunks` | 没 |
| 用户画像原文 | SQLite `user_profiles` 表 | 在 |
| 用户画像向量 | Qdrant `user_profile` | 没 |
| 用户记忆 | SQLite `user_memories` 表 | 在 |
| 对话记录 | SQLite `conversations` + `messages` 表 | 在 |

---

## 流程设计

```
用户改 Embedding 模型 → 保存 → 维度不匹配 → 弹框
  ┌─────────────────────────────────────────────┐
  │ 向量维度变更：1536 → 3072                   │
  │                                             │
  │ 旧数据与新模型不兼容，请选择处理方式：        │
  │                                             │
  │ [清除所有数据] ← 删除文档/切片/向量/记忆/对话 │
  │ [自动重建索引] ← 从 FTS5 回读切片重新入库     │
  └─────────────────────────────────────────────┘

  ↓ 清除所有数据                     ↓ 自动重建索引
  POST /clear-all-data              POST /rebuild-collections
    → 删 Qdrant collection             → 删旧 collection，建新的
    → 清空 FTS5                        → 从 FTS5 读切片，重新 embedding
    → 删 documents 表                  → 重建画像索引
    → 删 user_profiles 表              → 文档状态 → ready
    → 删 user_memories 表
    → 删 conversations + messages
    → 删 uploads/ 目录文件
    → 建空 collection（新维度）
```

---

## API

### `POST /api/settings/clear-all-data`

清空所有数据并重建空 collection。

**响应**:
```json
{
  "status": "cleared",
  "deleted": {
    "documents": 15,
    "chunks": 320,
    "memories": 5,
    "conversations": 3,
    "messages": 42
  }
}
```

### `POST /api/settings/rebuild-collections`（不变）

从 FTS5 回读切片自动重建向量索引。

---

## 对话框设计

| 按钮 | 颜色 | 操作 | toast |
|------|------|------|-------|
| 清除所有数据 | 红色/危险色 | 调 clear-all-data | "已清空 15 份文档、5 条记忆、3 个对话" |
| 自动重建索引 | 主色 | 调 rebuild-collections | "重建完成，15 份文档、320 个切片已处理" |

---

## 影响范围

| 文件 | 改动 |
|------|------|
| `backend/api/settings.py` | 新增 `clear-all-data` 端点 |
| `frontend/src/api/settings.ts` | 新增 `clearAllData()` + 类型 |
| `frontend/src/components/settings/SettingsPage.tsx` | 对话框双按钮 + handleClear |
