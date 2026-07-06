# P0 修复 — 入库并发控制 + 记忆系统 Qdrant 静默失败日志

**日期**: 2026-07-02 | **优先级**: P0 | **预计改动范围**: 2 文件

评审报告来源：`项目评审报告优化后.docx` — 负面评价 3.4 和 3.2

---

## P0-1: 入库并发控制

### 问题描述

`backend/rag/pipeline.py:84` — `asyncio.create_task(_bg_process())` 没有任何并发限制。

用户快速上传 100 个文件 → 100 个后台协程同时启动，每个都需要：
- 调用 embedding API（可能触发 rate limit）
- 写入 Qdrant（并发写入可能导致锁争用）
- 写入 SQLite FTS5
- 大量内存占用

### 设计方案

引入 `asyncio.Semaphore` 限制同时处理的后台任务数。

```
ingest_document(background=True)
  │
  └─ create_task(_bg_process())    ← 立即返回 doc_id，不阻塞 API
       │
       └─ async with semaphore:    ← 后台排队等待
            _process_document()    ← 含 1 次重试
```

关键决策：**信号量放在 `_bg_process()` 内部而非 `ingest_document()`**。这样 API 响应不受影响（仍然立即返回 `doc_id`），排队等待发生在后台协程中。信号量覆盖整个处理周期（含重试），确保重试期间也占槽位。

### 改动点

| 文件 | 改动 |
|------|------|
| `backend/config.py` | 新增 `ingestion_max_concurrency: int = 3` |
| `backend/rag/pipeline.py` | 模块级 `_ingestion_semaphore`，`_bg_process()` 入口 `async with` |

默认值 3：覆盖典型 embedding API rate limit（10-20 QPS），给检索等并发操作留余量。可通过 `.env` 的 `INGESTION_MAX_CONCURRENCY` 调整。

---

## P0-2: 记忆系统 Qdrant 静默失败

### 问题描述

`backend/memory/profile.py` 两处 `except Exception: pass` 静默吞掉异常：

- `_index_profile()` L104-105：Qdrant 写入失败时什么都不记录
- `search_profile()` L221-222：Qdrant 搜索失败时什么都不记录

后果：画像已保存到 SQLite 但 Qdrant 索引是旧版本 → `recall_memory` 工具搜索不到最新记忆 → 用户以为系统"忘了"，但数据实际在数据库中，排查无日志可查。

### 设计方案

行为不变（仍然不阻塞主流程），加 `logger.warning()` 日志：

| 位置 | 当前 | 改为 |
|------|------|------|
| `_index_profile()` L104-105 | `except Exception: pass` | `logger.warning("profile Qdrant index failed, recall_memory may return stale results", exc_info=True)` |
| `search_profile()` L221-222 | `except Exception: pass` | `logger.warning("profile Qdrant search failed, falling back to direct match", exc_info=True)` |

`exc_info=True` 输出完整 traceback，可直接定位根因（网络超时 / Qdrant 文件锁 / 集合不存在等）。

### 改动点

| 文件 | 改动 |
|------|------|
| `backend/memory/profile.py` | 2 处 `pass` → `logger.warning(...)` |

---

## 影响范围

无 API 变更，无前端变更，无数据库 schema 变更。不影响现有功能行为，仅限制并发数 + 增加日志输出。

## 验证方式

**P0-1 验证**：
1. 连续上传 5 个文档
2. 观察日志：应看到 3 个任务同时处理，另 2 个等待
3. 所有文档最终状态为 `ready`

**P0-2 验证**：
1. 故意停止 Qdrant 服务，发送记忆相关消息
2. 日志应出现 warning 而非静默
3. 功能正常降级（SQLite 数据仍在）
4. 运行现有测试：`pytest backend/tests/ -v`
