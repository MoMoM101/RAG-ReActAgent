# 批次 2：文档上传 SSE + 记忆保存前置拦截 — 设计文档

## #1 — 文档上传进度 SSE（替代轮询）

### 现状
- 前端 `documentStore.ts` 上传后用 2s 递归轮询 `GET /api/documents`
- 前端 `DocumentList.tsx` 用 2s 定时器轮询所有处理中文档
- 后端 `_process_document` 依次更新 `doc.status`: uploaded → parsing → chunking → embedding → indexing → ready/failed

### 改动

**后端新增：`backend/rag/progress.py`**
- `ProgressManager` 类，管理 `dict[doc_id, list[Queue]]` 订阅关系
- `publish(doc_id, event)` — 向该文档所有订阅者推送事件
- `subscribe(doc_id)` — 返回 `asyncio.Queue`，超时 5 分钟自动清理

**后端新增：`GET /api/documents/{doc_id}/progress`**
- SSE 端点，`text/event-stream`
- 从 `ProgressManager.subscribe(doc_id)` 获取队列
- 逐条 yield SSE 事件，`done` 或 `error` 后断开

**后端修改：`backend/rag/pipeline.py` — `_process_document`**
- 每次 `await session.commit()` 状态变更后，调用 `progress.publish(doc_id, {...})`

**前端修改：`frontend/src/api/documents.ts`**
- 新增 `subscribeProgress(docId: string, onEvent: callback)` 函数，封装 EventSource

**前端修改：`frontend/src/stores/documentStore.ts`**
- `upload()` 方法中，上传后调用 `subscribeProgress(doc_id, callback)` 替代轮询
- 回调中：更新文档状态、收到 done/error 后关闭 EventSource、刷新列表

**前端修改：`frontend/src/components/documents/UploadZone.tsx`**
- 接收 `progress` prop，展示实时步骤而非静态"上传处理中..."

**前端修改：`frontend/src/components/documents/DocumentList.tsx`**
- 移除 `useEffect` 中的 2s 轮询定时器
- 处理中文档的状态由 SSE 事件驱动更新

### 数据流
```
上传 → 返回 doc_id → 前端 GET /api/documents/{doc_id}/progress (SSE)
                           ↓
_process_document 每步 publish → Queue → SSE → EventSource
                           ↓
                    前端实时渲染步骤变化
                           ↓
                    done/error → 关闭 SSE → 刷新列表
```

---

## #7 — 记忆保存前置拦截（正则匹配）

### 现状
- `agent/context.py` 系统提示词"铁律2"让 LLM 调 `save_to_memory`
- `agent/classifier.py` 检测个人信息标记，但不强制执行
- 弱模型可能跳过工具调用，只口头说"记住了"

### 改动

**后端新增：`backend/agent/intercept.py`**
- `intercept_memory(query: str) -> str | None`
  - 匹配显式声明句式：`我叫XXX`、`我是XXX`
  - 匹配偏好/决定句式：`我喜欢XXX`、`我习惯XXX`、`我决定XXX`、`我项目是XXX`
  - 匹配到则返回格式化的记忆内容（如"用户叫XXX"），否则返回 `None`

**后端修改：`backend/agent/loop.py` — `run_agent_loop`**
- 在构建 messages 之前，调用 `intercept_memory(user_message)`
- 命中则直接 `await registry.execute("save_to_memory", content=memory_content)`
- 命中后在 system_msg.content 末尾追加"已自动保存"提示，让 LLM 只需回应确认

**前端：无改动**

### 数据流
```
用户消息 → intercept_memory() 正则匹配
         ├─ 命中 → save_to_memory → LLM 回应确认
         └─ 未命中 → 正常 Agent Loop（LLM 自主判断）
```
