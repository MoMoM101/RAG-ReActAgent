# 二期 Agent 工具化 — 设计文档

**日期**: 2026-06-25 | **状态**: 已确认

---

## 1. 概述

在一期 MVP 基础上，扩展 Agent 工具能力：新增 3 个内置工具，支持可配置的工具重试策略，提供清晰的工具扩展点。

---

## 2. 新增工具

### 2.1 calculator

- **触发场景**: 用户提出数值计算需求
- **实现**: 使用 `ast.parse` 安全解析表达式，白名单只允许 `+ - * / ( )` 运算符和数字字面量，拒绝 `Call`/`Attribute`/`Name` 节点
- **重试策略**: `none`（本地计算，重试无意义）

### 2.2 list_documents

- **触发场景**: "有哪些文档" / "知识库有什么"
- **实现**: 异步查询 SQLite documents 表，返回 `[{id, filename, file_type, status, created_at}]`
- **重试策略**: `exponential`，max 2（DB 查询，偶发锁可重试）

### 2.3 get_document_info

- **触发场景**: "某个文档的详情" / "有多少切片"
- **参数**: `document_id`
- **实现**: 查询单文档全部字段 + 切片数量
- **重试策略**: `exponential`，max 2

三个工具均继承 `BaseTool`，注册到全局 `ToolRegistry`。

---

## 3. 重试配置

### 3.1 策略

在 `BaseTool` 增加 `retry_strategy` 字段：

- `"exponential"` — 指数退避重试（1s, 2s, 4s...）
- `"none"` — 不重试，立即返回失败

### 3.2 优先级

1. 工具自身 `max_retries` 作为默认值
2. `settings.max_tool_retries` 作为全局上限，取 `min(tool.max_retries, settings.max_tool_retries)`

### 3.3 各工具默认值

| 工具 | retry_strategy | max_retries | 理由 |
|------|---------------|-------------|------|
| search_docs | exponential | 3 | 网络请求，值得重试 |
| calculator | none | 0 | 纯本地计算 |
| list_documents | exponential | 2 | DB 查询，偶发锁可重试 |
| get_document_info | exponential | 2 | DB 查询 |

---

## 4. 扩展点

- **新工具接入**: 写一个 `BaseTool` 子类 + 一行 `registry.register()`，无需改其他文件
- **classifier**: `suggested_tools` 列表自动匹配注册的工具名，只需为新工具添加关键词识别
- **system prompt**: `tools_desc` 已从 registry 动态拼接，新工具自动出现

---

## 5. 涉及的文件

| 文件 | 改动 |
|------|------|
| `backend/agent/tools.py` | 新增 3 个 Tool 类；BaseTool 加 retry_strategy；ToolRegistry.execute 读策略 |
| `backend/agent/classifier.py` | 添加新工具关键词识别 |
| `backend/agent/loop.py` | 无需改动 |
| `backend/agent/context.py` | 无需改动 |
| `backend/config.py` | 无需改动 |

---

## 6. 安全

- **calculator**: `ast.parse` + 白名单节点类型，拒绝任意代码执行
- **list_documents/get_document_info**: 走 SQLAlchemy ORM 参数绑定，已有 SQL 注入防护
