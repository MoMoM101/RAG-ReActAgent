# 应用日志系统设计

**日期**: 2026-07-02 | **优先级**: P3 | **预计改动范围**: 6 文件

---

## 问题描述

项目目前 **没有应用日志配置**。现有模块使用 `logging.getLogger(__name__)`，全部输出到 stderr，没有落盘。此外，三个关键路径的日志覆盖严重不足：

| 路径 | 现有日志 | 缺失 |
|------|----------|------|
| 文档切块+向量化 | pipeline 有 started/complete | embedding 耗时、indexing 耗时 |
| 记忆入库 | **零日志**（除 P0-2 刚加的失败告警） | 拦截捕获、facts 写入、会话提取 |
| 召回+精排 | **零日志** | 语义/关键词命中数、RRF 候选数、去重移除数、精排前后变化 |

## 设计方案

### Part A — 基础设施：日志落盘

### 总体架构

```
┌──────────┐  ┌──────────┐  ┌──────────┐  ┌──────────┐
│ main.py  │  │pipeline.py│ │profile.py│  │ tools.py │
│ logger   │  │ logger   │  │ logger   │  │ logger   │
└────┬─────┘  └────┬─────┘  └────┬─────┘  └────┬─────┘
     │              │              │              │
     └──────────────┴──────────────┴──────────────┘
                        │
                   根 logger
                   (setup_logging 配置)
                    │         │
               ┌────▼───┐ ┌──▼──────────┐
               │Console │ │RotatingFile │
               │Handler │ │Handler      │
               │(text)  │ │(JSON lines) │
               │stderr  │ │app.log      │
               └────────┘ └─────────────┘
```

### 核心决策

**双输出，双格式**：
- **Console**：人可读的纯文本格式，开发时直接在终端看
- **File**：JSON Lines 格式，方便监控系统解析（`jq`、Filebeat、Promtail 等）

**不替换现有 `logger.getLogger(__name__)` 用法**。只需在启动时配置根 logger，所有模块日志自动流入两个 handler。

### 日志格式

**Console 格式**（人可读）：

```
2026-07-02 14:30:01 WARNING  [memory.profile] profile Qdrant index failed, recall_memory may return stale results
```

**File 格式**（JSON Lines，一行一条）：

```json
{"ts": "2026-07-02T06:30:01.123Z", "level": "WARNING", "logger": "memory.profile", "msg": "profile Qdrant index failed, recall_memory may return stale results", "module": "profile", "func": "_index_profile", "line": 108}
```

带异常堆栈时追加 `"exc"` 字段：

```json
{..., "exc": "Traceback (most recent call last):\n  File ...\n  ..."}
```

### 文件轮转

`RotatingFileHandler`：单文件最大 **10MB**，保留 **5** 个历史文件。

```
data/logs/
├── app.log        ← 当前
├── app.log.1      ← 上一轮
├── app.log.2
├── app.log.3
├── app.log.4
├── app.log.5      ← 最旧，下一轮被覆盖
└── access.log     ← 已有（middleware/logging.py）
```

### 日志级别控制

通过 `.env` 的 `LOG_LEVEL` 控制，默认 `INFO`。开发调试时可设 `DEBUG`。

---

## 改动点

| 文件 | 改动 | 说明 |
|------|------|------|
| `backend/config.py` | 新增 `log_level: str = "INFO"` | .env 可控 |
| `backend/logging_config.py` | **新建文件** | ~50 行的 `setup_logging()` 函数 |
| `backend/main.py` | lifespan 开头调用 `setup_logging()` | 1 行 |

### `logging_config.py` 结构

```python
def setup_logging() -> None:
    # 1. 读配置
    # 2. 创建 ConsoleHandler（text 格式，stderr）
    # 3. 创建 RotatingFileHandler（JSON 格式，data/logs/app.log）
    # 4. 挂到根 logger
    # 5. 设日志级别
```

- ConsoleHandler 用标准 `logging.Formatter`
- FileHandler 用自定义 `JsonFormatter`（继承 `logging.Formatter`，重写 `format()` 返回 JSON 字符串）
- 使用 `logging.getLogger().addHandler()` 而非 `dictConfig`，保持代码可读

---

### Part B — 业务日志补齐：按观察价值取舍

原则：**高频路径打 1 条汇总，低频路径打关键节点，成功静默失败告警**。

#### 路径 1：文档切块 + 向量化（pipeline.py，低频）

已有 `started` / `chunking done` / `embedding started` / `complete`，补 2 条耗时：

```
[已有] ingestion started  doc_id=... filename=...
[已有] chunking done      doc_id=... chunks=12
[新增] embedding done     doc_id=... elapsed_ms=2340 vectors=12
[新增] indexing done      doc_id=... elapsed_ms=180
[已有] ingestion complete doc_id=... elapsed_ms=3100
```

#### 路径 2：记忆入库（profile.py，低频）

当前仅失败时告警，补 3 条正常操作日志：

```
[新增] memory facts appended     total=8 new=2
[新增] memory session extracted  total=5 new=1
[新增] memory index rebuilt      vectors=12
[已有] profile Qdrant index failed  ...  ← P0-2 刚加的 warning
```

去掉 `handle_intercept`（太频繁）和 `_index_profile` 成功日志（无观察价值）。

#### 路径 3：召回 + 精排（retriever.py，高频）

当前零日志。5 条太多，打 1 条汇总 INFO，细节可临时开 DEBUG：

```
[新增] search semantic=12 keyword=8 fused=36 reranked=8 final=8 elapsed_ms=320
```

---

## 改动点汇总

| 文件 | 改动 | 说明 |
|------|------|------|
| `backend/config.py` | 新增 `log_level: str = "INFO"` | .env 可控 |
| `backend/logging_config.py` | **新建** | `setup_logging()` + `JsonFormatter`，约 50 行 |
| `backend/main.py` | lifespan 开头调用 `setup_logging()` | 1 行 |
| `backend/rag/pipeline.py` | 新增 2 条 INFO | embedding/indexing 耗时 |
| `backend/rag/retriever.py` | 新增 1 条 INFO | 检索全链路汇总 |
| `backend/memory/profile.py` | 新增 3 条 INFO | facts/session/rebuild |

共 6 个文件，6 条新业务日志 + 基础设施，零额外依赖。

---

## 不在范围内

- 不改造 `middleware/logging.py` 的 access log
- 不引入第三方日志库
- 不加 request_id 注入（后续可加）

## 验证方式

1. 启动后端，终端出现启动确认日志
2. 上传文档 → `app.log` 出现完整耗时链条
3. 发送聊天 → `app.log` 出现 search 汇总
4. 触发记忆 → `app.log` 出现 memory 日志
5. `cat data/logs/app.log | python -m json.tool` 验证 JSON
6. `pytest backend/tests/ -v` 确认无回归
