# Rebuild 切块自适应重构设计

> 日期：2026-07-02
> 状态：待评审
> 参考：docs/CHUNK_EMBEDDING_CONFLICT.md

---

## 问题

切换 embedding 模型后 rebuild 时，FTS5 回读的旧 chunk 可能超过新模型 `max_input`，导致 embed API 报错。最坏情况下 FTS5 被清空而 Qdrant 为空 — 知识库不可用。

---

## 解决方案总览

| 环节 | 做什么 | 解决什么问题 |
|------|--------|-------------|
| 存储原始文本 | 入库时存 `raw_text`，rebuild 时重新切分 | 换模型后 chunk 大小可重切 |
| 自适应 chunk_size | pre-flight 试 embed 确定实际可用大小 | 不依赖模型文档的 max_input |
| 双缓冲写入 | 建 `_v2` 表写入 → 完成后切换 | rebuild 期间搜索不中断 |
| 异步+进度推送 | rebuild 后台执行，进度 SSE 推送 | 大数据量不阻塞 API |

---

## 1. 存储原始文本

### Document 表新增字段

```sql
ALTER TABLE documents ADD COLUMN raw_text TEXT;
ALTER TABLE documents ADD COLUMN chunk_size INTEGER;
```

### 入库流程变更 (`pipeline.py`)

```
旧: load_document → split_text → embed → index
新: load_document → 写 doc.raw_text → split_text → embed → index
                └── 保留原始文本，不再随 chunk 切分丢失
```

`raw_text` 只在入库时写入一次，后续不再更新。已入库的无 `raw_text` 文档，rebuild 时降级为从 FTS5 回读旧 chunk 做 pre-flight 校验。

---

## 2. 自适应 chunk_size（pre-flight）

### 流程

```
rebuild 开始
  │
  ▼
找样本：所有文档中最长的那段 raw_text
（无 raw_text 的从 FTS5 回读旧 chunk，取最长一条文本）
  │
  ▼
chunk_size = settings.chunk_size（当前用户配置值）
  │
  ▼
┌── split_text(sample, chunk_size) → 取最长 chunk
│     │
│     ▼
│   试 embed 这个 chunk
│     │  成功 → 确定 chunk_size，全量 rebuild
│     │  失败 → chunk_size = chunk_size * 2 // 3（减 1/3）
│     │         e.g. 512→341→227→151→100→66→44
│     │         重试，最多 10 轮
│     └── 10 轮仍未成功（chunk_size ≈ 8）→ 报错退出
```

Pre-flight 只花 1 次 API 调用（正常情况），失败时每降级一轮多 1 次调用。

### 日常入库同样处理

`_process_document()` embed 失败时也走同样的降级重试 — 防止用户设了大 chunk_size 后新上传文档直接入库失败。成功后把降级后的 `chunk_size` 写回 `settings.chunk_size`。

---

## 3. 双缓冲写入

### 中间表

| 用途 | 日常表 | rebuild 写入表 |
|------|--------|---------------|
| Qdrant 向量 | `rag_chunks` | `rag_chunks_v2` |
| FTS5 全文 | `chunks_fts` | `chunks_fts_v2` |
| 用户画像 | `user_profile` | `user_profile_v2` |

### 流程

```
1. 删除已存在的 _v2 表（上次失败残留）
2. 创建 _v2 Qdrant collection（新维度）
3. pre-flight 确定 chunk_size
4. 逐文档: raw_text → split_text(chunk_size) → embed → upsert Qdrant _v2
   + 写入 FTS5 _v2
   + 推送进度: {status: "rebuilding", current: N, total: M, filename: "xxx.pdf"}
5. 画像: 写入 user_profile_v2
6. 切换:
   Qdrant — 删旧 rag_chunks → 按新维度创建 rag_chunks → upsert _v2 数据 → 删 _v2
   FTS5  — DROP chunks_fts → ALTER chunks_fts_v2 RENAME TO chunks_fts
   画像  — DROP user_profile → user_profile_v2 同 Qdrant 流程
7. 更新 Document 表: chunk_count / embedding_model / embedding_dim / chunk_size
8. 进度推送: {status: "completed", chunk_count: N, actual_chunk_size: M}
```

### 为什么 Qdrant 这边是 copy 而不是 rename

Qdrant 不支持 rename collection。所以流程是删旧主表 → 建新主表 → _v2 data copy。切换窗口（删建 + copy time）取决于数据量，预估 1000 chunks 在 2 秒以内。这段时间内收到的搜索请求会查不到数据（返回空结果），不会崩溃。

### 失败处理

rebuild 中途失败时：
- `_v2` 表和旧主表都保留
- 进度推送 `{status: "failed", error: "..."}`
- 下次触发 rebuild 时 step 1 清除残留的 `_v2` 重新来
- 旧主表始终未动，搜索不受影响

---

## 4. 异步 + 进度推送

### API

```
POST /api/settings/rebuild-collections
  → 立即返回 {status: "started"}
  → 后台 asyncio.create_task 执行
```

### 进度事件

复用现有 `rag/progress.py` 的 `progress.publish()` 机制，新增 rebuild 专用事件类型：

```json
{"status": "preflight",   "message": "正在检测模型兼容性..."}
{"status": "rebuilding",  "current": 5, "total": 20, "filename": "报告.pdf"}
{"status": "switching",   "message": "正在切换索引..."}
{"status": "completed",   "chunk_count": 1234, "actual_chunk_size": 341}
{"status": "failed",      "error": "..."}
```

前端通过现有 `/api/documents/progress-sse` 端点监听进度。

---

## 5. 改动清单

| 文件 | 改动 |
|------|------|
| `models/orm.py` | Document 新增 `raw_text` (Text, nullable) + `chunk_size` (Integer, nullable) |
| `models/database.py` | `init_db()` 迁移新增两列 |
| `rag/pipeline.py` | `_process_document()` 存 `raw_text`；首次 embed 失败降级重试 |
| `rag/splitter.py` | 无改动 |
| `api/settings.py` | `rebuild_collections()` 重写：异步、pre-flight、双缓冲 |
| `api/documents.py` | 无需改动（进度 SSE 复用现有） |
| `rag/progress.py` | 无改动 |
| `config.py` | 无改动 |

不新增文件，总计改动约 4 个文件（含 orm+migration）。

---

## 6. 测试要点

| 场景 | 预期 |
|------|------|
| chunk_size 与新模型匹配 | pre-flight 一步通过，全量 rebuild |
| chunk_size 太大 | pre-flight 自动降级收敛到可用值 |
| rebuild 期间搜索 | 返回完整旧数据（FTS5+Qdrant 旧表未动） |
| rebuild 中途失败 | 旧数据无损，下次 rebuild 清除残留重新来 |
| 无 raw_text 的旧文档 | FTS5 回读旧 chunk 做 pre-flight；若超限则跳过并标记 |
| 日常入库 chunk_size 不匹配 | embed 失败后自动降级重试 |
