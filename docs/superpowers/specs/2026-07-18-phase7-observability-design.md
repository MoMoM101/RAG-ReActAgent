# Phase 7: Observability — Metrics Completion & Prometheus Endpoint

> 日期：2026-07-18
> 决策：Option A — 只补全缺失指标 + 暴露 /metrics 端点，不引入 Grafana/Prometheus 全套监控栈
> 基线：Phase 0–6 完成，658/663 测试通过

## 1. 目标

在现有 399 行 `metrics.py` 基础上补全 4 项指标，暴露标准 `/metrics` 端点，使单机 SQLite 部署下通过 `curl /metrics` 即可获取完整系统状态。

### 1.1 范围

- 新增 4 个指标方法（SSE 连接、流事件、队列深度、最老任务年龄）
- 暴露 `GET /metrics` Prometheus text 格式端点
- 在 SSE 流式响应中插入轻量 hook（~15 行）
- 新增 ~30 行测试

### 1.2 非目标

- 不安装 Prometheus / Grafana / Alertmanager
- 不定义 SLO / alert rules
- 不创建 Grafana dashboard
- 不修改 `tracing.py` 或 `middleware/logging.py`
- 不引入 OpenTelemetry SDK 依赖

## 2. 设计

### 2.1 新增指标

| 指标 | 方法 | Prometheus name | 类型 |
|---|---|---|---|
| SSE 连接事件 | `record_sse_connection(event)` | `rag_sse_connections_total{event="open\|done\|disconnect"}` | counter |
| SSE 流事件 | `record_stream_event(event_type)` | `rag_stream_events_total{type="answer_chunk\|sources\|verification\|done\|error"}` | counter |
| 文档队列深度 | `collect_system_state(session)` | `rag_ingestion_queue_depth{status="pending\|running\|retry_wait"}` | gauge |
| 最老任务年龄 | `collect_system_state(session)` | `rag_oldest_task_age_seconds` | gauge |

### 2.2 SSE 插桩点

在 `backend/api/chat.py` 的 SSE 生成器中：

```python
# 连接打开
get_metrics().record_sse_connection("open")

try:
    async for event in generate_sse():
        # 按 event 类型计数
        if "answer_chunk" in event:
            get_metrics().record_stream_event("answer_chunk")
        elif "sources" in event:
            get_metrics().record_stream_event("sources")
        elif "verification" in event:
            get_metrics().record_stream_event("verification")
        elif "done" in event:
            get_metrics().record_stream_event("done")
        yield event
except Exception:
    get_metrics().record_stream_event("error")
    raise
finally:
    # 正常结束
    get_metrics().record_sse_connection("done")
```

### 2.3 /metrics 端点

新建 `backend/api/metrics.py`：

```python
from fastapi import APIRouter, Response
from metrics import export_prometheus

router = APIRouter(tags=["metrics"])

@router.get("/metrics")
async def metrics():
    return Response(content=export_prometheus(), media_type="text/plain")
```

在 `main.py` 中注册：
```python
from api.metrics import router as metrics_router
app.include_router(metrics_router)
```

### 2.4 队列深度 / 任务年龄

`collect_system_state()` 在 `export_prometheus()` 被调用时查询数据库。使用同步 sqlite3 查询而非 async session，避免 `export_prometheus()` 变成异步函数，保持 Prometheus 导出路径简单。

```python
def _collect_system_state(db_path: str) -> dict:
    """Query system state for gauge metrics."""
    import sqlite3
    import time
    conn = sqlite3.connect(db_path)
    try:
        # Queue depth by status
        rows = conn.execute(
            "SELECT status, COUNT(*) FROM task_queue "
            "WHERE status IN ('pending','running','retry_wait') GROUP BY status"
        ).fetchall()
        queue_depth = {row[0]: row[1] for row in rows}

        # Oldest non-terminal task age
        row = conn.execute(
            "SELECT created_at FROM task_queue "
            "WHERE status IN ('pending','running','retry_wait') "
            "ORDER BY created_at ASC LIMIT 1"
        ).fetchone()
        if row and row[0]:
            # created_at is 'YYYY-MM-DD HH:MM:SS' (SQLite datetime)
            try:
                created = time.strptime(row[0], "%Y-%m-%d %H:%M:%S")
                oldest_age = time.time() - time.mktime(created)
            except ValueError:
                oldest_age = 0.0
        else:
            oldest_age = 0.0

        return {"queue_depth": queue_depth, "oldest_task_age_seconds": oldest_age}
    finally:
        conn.close()
```

在 `export_prometheus()` 末尾追加 gauge 输出。

## 3. 文件改动

| 文件 | 操作 | 行数 | 内容 |
|---|---|---|---|
| `backend/metrics.py` | 修改 | +40 | 4 个新方法 + `_collect_system_state()` + Prometheus 导出新指标 |
| `backend/api/metrics.py` | 新建 | +15 | FastAPI router，GET /metrics |
| `backend/main.py` | 修改 | +3 | 注册 metrics router |
| `backend/api/chat.py` | 修改 | +15 | SSE 插桩 hook |
| `backend/tests/test_metrics.py` | 新建 | +30 | Prometheus 格式验证 + 计数器正确性 |

总计约 103 行。

## 4. 验证

- `/metrics` 端点返回 200 + `text/plain`
- Prometheus 输出包含所有已有指标 + 4 个新指标
- `rag_sse_connections_total{event="done"}` 在完成一次问答后 > 0
- `rag_stream_events_total{type="answer_chunk"}` > 0
- `rag_ingestion_queue_depth{status="pending"}` 在有未处理文档时 > 0
- 全量回归测试通过
