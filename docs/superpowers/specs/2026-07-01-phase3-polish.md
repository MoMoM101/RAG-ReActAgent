# Phase 3 — 锦上添花设计

**日期**: 2026-07-01 | **优先级**: 低 | **预计改动范围**: 5+ 文件

---

## 3.1 Rate Limiting（接口限流）

### 问题
无任何速率限制，恶意或意外高频请求可直接耗尽 LLM token 额度。

### 方案
使用 `slowapi`（FastAPI 兼容），对聊天接口做限制。

| 端点 | 限制 | 原因 |
|------|------|------|
| `POST /api/chat` | 30/min | LLM 调用昂贵，防止刷 token |
| `POST /api/documents/upload` | 10/min | 上传需索引，消耗资源 |
| `GET /api/health` | 无限制 | 健康检查无成本 |

存储：默认内存（不引入 Redis 依赖）。

**文件**:
- `backend/main.py` — 挂载 `SlowAPIMiddleware` + 配置 limiter
- `backend/api/chat.py` — 加 `@limiter.limit` 装饰器
- `backend/api/documents.py` — 加 `@limiter.limit` 装饰器
- `requirements.txt` — 加 `slowapi`

---

## 3.2 trim_messages 内容 hash 去重

### 问题
`context.py:147` 用 `id(m)`（Python 对象内存地址）做去重判断：

```python
kept_ids = {id(m) for m in kept}
for m in others:
    if m.role == "user" and id(m) not in kept_ids and m.content:
```

同一个消息对象不会被重复丢弃所以当前"能用"，但逻辑上 `id()` 不可靠——如果消息被复制或重建，相同的消息内容会有不同的 `id`。

### 方案
改为内容哈希：

```python
import hashlib

def _msg_hash(msg: ChatMessage) -> str:
    return hashlib.md5(
        f"{msg.role}:{msg.content}".encode()
    ).hexdigest()

kept_ids = {_msg_hash(m) for m in kept}
```

**文件**: `backend/agent/context.py`

---

## 3.3 后台 Ingestion 监控日志

### 问题
`worker/ingestion.py` 处理文档入库的后台任务没有任何状态暴露。文档卡在 "parsing" 状态 30 分钟才被超时清理，运维无感知。

### 方案
在 worker 的关键节点加结构化日志：

| 节点 | 日志内容 |
|------|----------|
| 任务入队 | `logger.info("ingestion started", doc_id=..., filename=...)` |
| 各阶段完成 | `logger.info("phase done", doc_id=..., phase="chunking", chunks=42)` |
| 任务失败 | `logger.error("ingestion failed", doc_id=..., error=...)` |
| 任务成功 | `logger.info("ingestion complete", doc_id=..., elapsed_ms=...)` |
| 超时清理 | `logger.warning("stale doc cleaned", doc_id=..., stuck_phase=...)` |

同时加一个简单的 `/api/admin/queue-stats` 端点返回各状态文档数量。

**文件**:
- `backend/worker/ingestion.py` — 加结构化日志
- `backend/api/documents.py` — 加 queue-stats 端点
- `backend/main.py` — 注册路由

---

## 影响范围

| # | 改动 | 文件 | 类型 |
|---|------|------|------|
| 3.1 | Rate limiting | `main.py`, `api/chat.py`, `api/documents.py`, `requirements.txt` | 后端 |
| 3.2 | trim_messages hash | `agent/context.py` | 后端 |
| 3.3 | 后台日志 + queue-stats | `worker/ingestion.py`, `api/documents.py` | 后端 |

---

## 实施顺序

3.2 → 3.3 → 3.1

先做最独立的小改动（3.2），再做日志（3.3），最后做需要安装依赖的限流（3.1）。
