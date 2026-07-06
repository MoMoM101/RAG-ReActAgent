# 批次 2：文档上传 SSE + 记忆保存前置拦截 — 实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 文档上传进度 SSE 替代轮询 + 个人信息正则前置拦截 save_to_memory

**Architecture:** #1 新增 ProgressManager 管理发布-订阅，_process_document 每步 publish，SSE 端点推送到前端 EventSource。#2 新增 intercept.py 正则匹配，在 Agent Loop 调用 LLM 前拦截并直接执行 save_to_memory。

**Tech Stack:** FastAPI SSE, asyncio.Queue, EventSource, React, TypeScript

---

### Task 1: 新建 `backend/rag/progress.py` — ProgressManager

**Files:**
- Create: `backend/rag/progress.py`

- [ ] **Step 1: 编写 ProgressManager 类**

```python
"""Progress manager for document processing — publish/subscribe via asyncio.Queue."""

import asyncio
import time


class ProgressManager:
    def __init__(self):
        self._subscribers: dict[str, list[asyncio.Queue]] = {}

    def publish(self, doc_id: str, event: dict) -> None:
        """推送事件到该文档的所有订阅者。"""
        queues = self._subscribers.get(doc_id, [])
        for q in queues:
            try:
                q.put_nowait(event)
            except asyncio.QueueFull:
                pass  # 订阅者没在消费，跳过

    async def subscribe(self, doc_id: str, timeout: float = 300) -> asyncio.Queue:
        """订阅文档进度。返回一个 Queue，超时自动清理。"""
        q: asyncio.Queue = asyncio.Queue(maxsize=50)
        self._subscribers.setdefault(doc_id, []).append(q)
        return q

    def unsubscribe(self, doc_id: str, q: asyncio.Queue) -> None:
        """取消订阅并清理。"""
        queues = self._subscribers.get(doc_id, [])
        if q in queues:
            queues.remove(q)
        if not queues:
            self._subscribers.pop(doc_id, None)


# 全局单例
progress = ProgressManager()
```

- [ ] **Step 2: 验证导入**

```bash
python -c "from rag.progress import progress; print('OK')"
```

- [ ] **Step 3: 提交**

```bash
git add backend/rag/progress.py
git commit -m "feat: add ProgressManager for SSE document processing events"
```

---

### Task 2: `pipeline.py` — `_process_document` 每步发布进度事件

**Files:**
- Modify: `backend/rag/pipeline.py:89-145`

- [ ] **Step 1: 在每次状态变更后发布事件**

修改 `_process_document`，在每次 `await session.commit()` 之后插入 `progress.publish()`。替换整个函数体：

```python
async def _process_document(doc_id: str, file_path: str, file_type: str):
    from rag.progress import progress

    async with async_session() as session:
        result = await session.execute(select(Document).where(Document.id == doc_id))
        doc = result.scalar_one()

        # Parse
        doc.status = DocStatus.parsing
        await session.commit()
        progress.publish(doc_id, {"status": "parsing", "message": "正在解析文档..."})

        raw_text = load_document(file_path, file_type)

        # Chunk
        doc.status = DocStatus.chunking
        await session.commit()
        progress.publish(doc_id, {"status": "chunking", "message": "正在切分文本..."})

        chunks = split_text(raw_text, settings.chunk_size, settings.chunk_overlap)

        if not chunks:
            progress.publish(doc_id, {"status": "failed", "error": "No text extracted from document"})
            raise ValueError("No text extracted from document")

        # Embed
        doc.status = DocStatus.embedding
        await session.commit()
        progress.publish(doc_id, {
            "status": "embedding",
            "message": f"正在向量化 ({len(chunks)} 个分块)...",
            "chunk_count": len(chunks),
        })

        embedding = create_embedding()
        texts = [c.text for c in chunks]
        vectors = await embedding.embed(texts)

        # Index
        doc.status = DocStatus.indexing
        await session.commit()
        progress.publish(doc_id, {"status": "indexing", "message": "正在写入索引..."})

        vectordb = await create_vectordb()
        fts = SQLiteFTS5()
        await vectordb.delete_by_document(doc_id)
        await fts.delete_by_document(doc_id)

        points = []
        for chunk, vector in zip(chunks, vectors):
            chunk_id = str(uuid.uuid5(uuid.NAMESPACE_DNS, f"{doc_id}:{chunk.chunk_index}"))
            points.append({
                "id": chunk_id,
                "vector": vector,
                "payload": {
                    "document_id": doc_id,
                    "chunk_index": chunk.chunk_index,
                    "text": chunk.text,
                },
            })
            await fts.insert(chunk_id, doc_id, chunk.text)

        await vectordb.upsert(points)

        # Done
        doc.status = DocStatus.ready
        doc.chunk_count = len(chunks)
        doc.embedding_model = settings.embedding_model
        doc.embedding_dim = settings.embedding_dim
        await session.commit()
        progress.publish(doc_id, {
            "status": "ready",
            "chunk_count": len(chunks),
            "message": "入库完成",
        })
```

同时处理 `_bg_process` 中的失败路径。修改 `_bg_process` 内部函数（`ingest_document` 的 `_bg_process`，约第 58-72 行），在 exception 分支也发布 error：

```python
        async def _bg_process():
            from rag.progress import progress
            for attempt in range(2):
                try:
                    await _process_document(doc_id, file_path, file_type)
                    return
                except Exception as e:
                    if attempt == 0:
                        await asyncio.sleep(10)
                    else:
                        async with async_session() as session:
                            result = await session.execute(select(Document).where(Document.id == doc_id))
                            doc = result.scalar_one()
                            doc.status = DocStatus.failed
                            doc.error_message = f"[重试1次后失败] {e}"
                            await session.commit()
                        progress.publish(doc_id, {"status": "failed", "error": str(e)[:200]})
```

- [ ] **Step 2: 验证**

```bash
python -c "from rag.pipeline import ingest_document, _process_document; print('Import OK')"
```

- [ ] **Step 3: 提交**

```bash
git add backend/rag/pipeline.py
git commit -m "feat: publish progress events during document processing"
```

---

### Task 3: 新增 SSE 端点 `GET /api/documents/{doc_id}/progress`

**Files:**
- Modify: `backend/api/documents.py`

- [ ] **Step 1: 添加 SSE 端点**

在 `documents.py` 末尾添加：

```python
import asyncio
from fastapi.responses import StreamingResponse


@router.get("/{doc_id}/progress")
async def document_progress(doc_id: str):
    from rag.progress import progress

    async def event_stream():
        q = await progress.subscribe(doc_id)
        try:
            # Push current status first
            async with async_session() as session:
                result = await session.execute(
                    select(Document).where(Document.id == doc_id)
                )
                doc = result.scalar_one_or_none()
                if doc:
                    yield f"data: {{\"status\": \"{doc.status.value}\", \"chunk_count\": {doc.chunk_count}}}\n\n"
                else:
                    yield f"data: {{\"status\": \"not_found\"}}\n\n"
                    return

            # Stream progress events
            while True:
                try:
                    event = await asyncio.wait_for(q.get(), timeout=30)
                    import json
                    yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
                    if event.get("status") in ("ready", "failed"):
                        break
                except asyncio.TimeoutError:
                    yield "data: {\"status\": \"timeout\"}\n\n"
                    break
        finally:
            progress.unsubscribe(doc_id, q)

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
```

- [ ] **Step 2: 验证导入**

```bash
python -c "from api.documents import router; print('SSE endpoint added'); print('Routes:', [r.path for r in router.routes])"
```

- [ ] **Step 3: 提交**

```bash
git add backend/api/documents.py
git commit -m "feat: add SSE endpoint for document processing progress"
```

---

### Task 4: 前端 — `subscribeProgress` + `documentStore` 改为 SSE

**Files:**
- Modify: `frontend/src/api/documents.ts`
- Modify: `frontend/src/stores/documentStore.ts`

- [ ] **Step 1: 在 `documents.ts` 添加 `subscribeProgress` 函数**

```typescript
export interface ProgressEvent {
  status: string;
  message?: string;
  chunk_count?: number;
  error?: string;
}

export function subscribeProgress(
  docId: string,
  onEvent: (event: ProgressEvent) => void,
  onDone: () => void,
): () => void {
  const es = new EventSource(`/api/documents/${docId}/progress`);

  es.onmessage = (e) => {
    try {
      const data = JSON.parse(e.data) as ProgressEvent;
      onEvent(data);
      if (data.status === "ready" || data.status === "failed" || data.status === "timeout") {
        es.close();
        onDone();
      }
    } catch {
      // ignore parse errors
    }
  };

  es.onerror = () => {
    es.close();
    onDone();
  };

  return () => es.close();
}
```

- [ ] **Step 2: 修改 `documentStore.ts` 的 `upload` 方法**

将轮询逻辑替换为 SSE 订阅。修改 `upload` 函数（约第 29-64 行）为：

```typescript
  upload: async (file: File) => {
    set({ uploading: true });
    try {
      const doc = await uploadDocument(file);
      const addToast = useToastStore.getState().addToast;
      addToast({ type: "success", message: `「${file.name}」上传成功` });

      // 用 SSE 替代轮询
      const { subscribeProgress } = await import("../api/documents");
      subscribeProgress(
        doc.id,
        (event) => {
          // 更新文档状态
          const docs = get().documents.map((d) =>
            d.id === doc.id
              ? { ...d, status: event.status, chunk_count: event.chunk_count ?? d.chunk_count }
              : d,
          );
          set({ documents: docs });
        },
        async () => {
          // SSE 结束（ready/failed/timeout），刷新完整列表
          await get().load();
          const updated = get().documents.find((d) => d.id === doc.id);
          if (updated?.status === "ready") {
            addToast({ type: "success", message: `「${file.name}」处理完成` });
          } else if (updated?.status === "failed") {
            addToast({ type: "error", message: `「${file.name}」处理失败: ${updated.error_message || "未知错误"}` });
          }
          set({ uploading: false });
        },
      );
    } catch (e: unknown) {
      const addToast = useToastStore.getState().addToast;
      if (e instanceof ApiError && e.status === 409) {
        addToast({ type: "error", message: `「${file.name}」已存在，无需重复上传` });
      } else {
        const msg = e instanceof Error ? e.message : "上传失败";
        addToast({ type: "error", message: msg });
      }
      await get().load();
      set({ uploading: false });
    }
  },
```

- [ ] **Step 3: 验证 TypeScript 编译**

```bash
cd frontend && npx tsc --noEmit
```

- [ ] **Step 4: 提交**

```bash
git add frontend/src/api/documents.ts frontend/src/stores/documentStore.ts
git commit -m "feat: replace document upload polling with SSE progress"
```

---

### Task 5: 前端 — `UploadZone` 展示实时步骤

**Files:**
- Modify: `frontend/src/components/documents/UploadZone.tsx`
- Modify: `frontend/src/stores/documentStore.ts` (暴露 progress 状态)

- [ ] **Step 1: 在 store 中暴露上传进度**

在 `documentStore.ts` 的 interface 和 store 中添加：

```typescript
interface DocumentStore {
  // ... 现有字段
  uploadProgress: { status: string; message?: string } | null;  // 新增
}
```

在 store 初始值中添加：

```typescript
  uploadProgress: null,
```

在 SSE 回调中更新 `uploadProgress`：

```typescript
  (event) => {
    set({ uploadProgress: { status: event.status, message: event.message } });
    // ... 现有更新逻辑
  },
```

在 finally 中清除：

```typescript
  set({ uploading: false, uploadProgress: null });
```

- [ ] **Step 2: 修改 `UploadZone.tsx`**

将静态 loading 状态替换为实时步骤展示。修改第 29-34 行：

```tsx
import { useDocumentStore } from "../../stores/documentStore";

const STEP_LABELS: Record<string, string> = {
  parsing: "正在解析文档...",
  chunking: "正在切分文本...",
  embedding: "正在向量化...",
  indexing: "正在写入索引...",
  ready: "处理完成",
  failed: "处理失败",
};

export function UploadZone() {
  const { upload, uploading, uploadProgress } = useDocumentStore();
  // ... 保留现有 dragging、handleDrop、handleFile 逻辑 ...

  return (
    <div
      className={`upload-zone ${dragging ? "dragging" : ""}`}
      onDrop={handleDrop}
      onDragOver={(e) => { e.preventDefault(); setDragging(true); }}
      onDragLeave={() => setDragging(false)}
      onClick={() => !uploading && document.getElementById("file-input")?.click()}
    >
      {uploading ? (
        <div style={{ display: "flex", flexDirection: "column", alignItems: "center", gap: 8 }}>
          <div style={{ width: 32, height: 32, borderRadius: "50%", border: "2px solid var(--accent)", borderTopColor: "transparent", animation: "spin 0.8s linear infinite" }} />
          <p style={{ fontSize: 13, color: "var(--fg)" }}>
            {uploadProgress?.message || "处理中..."}
          </p>
          {uploadProgress?.status && uploadProgress.status !== "ready" && uploadProgress.status !== "failed" && (
            <div className="progress-steps" style={{ display: "flex", gap: 16, fontSize: 12 }}>
              {["parsing", "chunking", "embedding", "indexing"].map((step, i) => {
                const stepIdx = ["uploaded", "parsing", "chunking", "embedding", "indexing"].indexOf(uploadProgress.status);
                const done = stepIdx > i;
                const active = stepIdx === i;
                return (
                  <span key={step} style={{
                    color: done ? "var(--green)" : active ? "var(--accent)" : "var(--muted)",
                    fontWeight: active ? 600 : 400,
                  }}>
                    {done ? "✓" : active ? "●" : "○"} {STEP_LABELS[step]}
                  </span>
                );
              })}
            </div>
          )}
        </div>
      ) : (
        // ... 保留现有的非上传状态内容 ...
      )}
      <input
        id="file-input"
        type="file"
        style={{ display: "none" }}
        accept={ACCEPT}
        multiple
        onChange={handleFile}
        disabled={uploading}
      />
    </div>
  );
}
```

- [ ] **Step 3: 验证编译**

```bash
cd frontend && npx tsc --noEmit
```

- [ ] **Step 4: 提交**

```bash
git add frontend/src/components/documents/UploadZone.tsx frontend/src/stores/documentStore.ts
git commit -m "feat: show real-time processing steps in UploadZone"
```

---

### Task 6: 前端 — 移除 `DocumentList` 中的轮询

**Files:**
- Modify: `frontend/src/components/documents/DocumentList.tsx`

- [ ] **Step 1: 删除轮询定时器**

删除第 41-49 行的 `useEffect` 块：

```typescript
  // 删除以下代码：
  // useEffect(() => {
  //   const hasProcessing = documents.some(
  //     (d) => d.status !== "ready" && d.status !== "failed",
  //   );
  //   if (!hasProcessing) return;
  //   const timer = setInterval(() => load(), 2000);
  //   return () => clearInterval(timer);
  // }, [documents, load]);
```

同时删除 `useEffect` 的 import（如果不再使用则从 react import 中移除 `useEffect`）。

- [ ] **Step 2: 保留主动加载和手动刷新**

确保 `load()` 在初始化 `useEffect` 中保留（第 37-39 行）：

```typescript
  useEffect(() => {
    load().finally(() => setLoading(false));
  }, [load]);
```

另外加一个手动刷新按钮（可选）：

```tsx
  <button className="doc-btn" onClick={() => load()} style={{ marginBottom: 8 }}>
    <RefreshIcon size={11} /> 刷新
  </button>
```

- [ ] **Step 3: 验证编译**

```bash
cd frontend && npx tsc --noEmit
```

- [ ] **Step 4: 提交**

```bash
git add frontend/src/components/documents/DocumentList.tsx
git commit -m "refactor: remove polling timer from DocumentList, use SSE-driven updates"
```

---

### Task 7: 新建 `backend/agent/intercept.py` — 记忆前置拦截 + LLM 确认

**Files:**
- Create: `backend/agent/intercept.py`

- [ ] **Step 1: 编写拦截 + 确认函数**

```python
"""Memory save pre-intercept — regex extraction + LLM confirmation."""

import re


def extract_memory_candidate(query: str) -> str | None:
    """正则提取个人信息候选。未命中返回 None。"""

    # 噪音词 — 误触发"我叫外卖"、"我是说…"等口语场景
    noise_words = {"外卖", "快递", "电话", "车", "说", "讲", "看一下",
                   "问一下", "睡了", "走了", "到了", "完了", "好了"}

    # 1. 显式声明：我叫/我是 XXX
    m = re.search(r"我(?:叫|是)\s*(.+)", query)
    if m:
        value = m.group(1).strip("，。,.").strip()
        if len(value) >= 1 and len(value) <= 20 and value not in noise_words:
            return f"用户叫{value}"

    # 2. 我（喜欢/习惯/决定/项目是）XXX
    m = re.search(r"我(?:喜欢|习惯|决定)\s*(.+)", query)
    if m:
        value = m.group(1).strip("，。,.").strip()
        if len(value) >= 2 and len(value) <= 20 and value not in noise_words:
            tag = "喜欢" if "喜欢" in query else ("习惯" if "习惯" in query else "决定")
            return f"用户{tag}{value}"

    # 3. 我的项目是 / 我在做 XXX
    m = re.search(r"我(?:项目是|在做)\s*(.+)", query)
    if m:
        value = m.group(1).strip("，。,.").strip()
        if len(value) >= 2 and len(value) <= 80 and value not in noise_words:
            return f"用户{value}（项目/当前工作）"

    return None


MEMORY_DECISION_TOOL = {
    "type": "function",
    "function": {
        "name": "decide_memory",
        "description": "判断一条候选信息是否值得存入长期记忆。",
        "parameters": {
            "type": "object",
            "properties": {
                "save": {
                    "type": "boolean",
                    "description": "值得保存则 true，否则 false",
                },
            },
            "required": ["save"],
        },
    },
}


async def confirm_memory(candidate: str) -> bool:
    """用 LLM (tool calling) 确认候选记忆是否值得保存。"""
    from llm.factory import create_llm
    from llm.base import ChatMessage

    system_prompt = """你是记忆保存确认器。判断信息是否值得存入长期记忆。

值得保存 → save=true:
- 可能在未来对话中用到的事实、偏好、决定
- 关于用户身份、职业、习惯的明确信息

不值得保存 → save=false:
- 临时性质的闲聊（"我叫外卖"、"我喜欢这首歌"）
- 模糊不确定的表述
- 常识性内容"""

    llm = create_llm()
    messages = [
        ChatMessage(role="system", content=system_prompt),
        ChatMessage(role="user", content=candidate),
    ]

    tool_calls_acc = []
    async for chunk in llm.chat_stream(messages, tools=[MEMORY_DECISION_TOOL]):
        if chunk.tool_calls:
            tool_calls_acc = chunk.tool_calls

    if tool_calls_acc:
        tc = tool_calls_acc[0]
        if tc.name == "decide_memory":
            return bool(tc.arguments.get("save", False))

    return False  # 无 tool call 或异常 → 安全兜底
```

- [ ] **Step 2: 验证正则提取**

```bash
python -c "
from agent.intercept import extract_memory_candidate
assert extract_memory_candidate('我叫馍馍') == '用户叫馍馍'
assert extract_memory_candidate('我喜欢Python') == '用户喜欢Python'
assert extract_memory_candidate('我决定换工作') == '用户决定换工作'
assert extract_memory_candidate('我叫外卖') is None  # 噪音词过滤
assert extract_memory_candidate('今天天气不错') is None
print('extract_memory_candidate tests passed')
"
```

- [ ] **Step 3: 验证 LLM 确认函数导入**

```bash
python -c "from agent.intercept import confirm_memory; print('Import OK')"
```

- [ ] **Step 4: 提交**

```bash
git add backend/agent/intercept.py
git commit -m "feat: add regex extract + LLM confirm memory pre-intercept"
```

---

### Task 8: `loop.py` — 集成前置拦截 + LLM 确认

**Files:**
- Modify: `backend/agent/loop.py`

- [ ] **Step 1: 在 Agent Loop 中插入拦截 + 确认逻辑**

在 `run_agent_loop` 函数中，intent classification 之后、build messages 之前插入：

```python
    # 1. Intent classification
    hint = classify_intent(user_message, conversation_history)
    yield {"event": "status", "data": {"message": "正在分析问题..."}}

    # 1.5. 记忆前置拦截：正则提取 → LLM 确认 → 保存
    from agent.intercept import extract_memory_candidate, confirm_memory
    candidate = extract_memory_candidate(user_message)
    if candidate:
        confirmed = await confirm_memory(candidate)
        if confirmed:
            await registry.execute("save_to_memory", content=candidate)
            hint.hint_text = (
                f"[系统] 已自动保存以下信息到长期记忆: {candidate}\n"
                + hint.hint_text
            )

    # 2. Build messages
```

- [ ] **Step 2: 验证导入**

```bash
python -c "from agent.loop import run_agent_loop; print('Import OK')"
```

- [ ] **Step 3: 提交**

```bash
git add backend/agent/loop.py
git commit -m "feat: integrate memory pre-intercept with LLM confirmation into agent loop"
```

---

### Task 9: 端到端验证

**Files:** (无改动)

- [ ] **Step 1: 全量 import 验证**

```bash
python -c "
from rag.progress import progress, ProgressManager
from agent.intercept import extract_memory_candidate, confirm_memory
from api.documents import router
from agent.loop import run_agent_loop
print('All imports OK')
"
```

- [ ] **Step 2: SSE Progress 后端验证**

```bash
python -c "
from rag.progress import progress
import asyncio

async def test():
    q = await progress.subscribe('test-doc-1')
    progress.publish('test-doc-1', {'status': 'chunking'})
    event = q.get_nowait()
    assert event['status'] == 'chunking'
    progress.unsubscribe('test-doc-1', q)
    print('SSE progress pub/sub - OK')

asyncio.run(test())
"
```

- [ ] **Step 3: extract_memory_candidate 测试**

```bash
python -c "
from agent.intercept import extract_memory_candidate

tests = [
    ('我叫馍馍', '用户叫馍馍'),
    ('我是软件工程师', '用户叫软件工程师'),
    ('我喜欢Python', '用户喜欢Python'),
    ('我习惯早起', '用户习惯早起'),
    ('我决定换工作', '用户决定换工作'),
    ('我叫外卖', None),       # 噪音词
    ('今天天气怎么样', None),
    ('hello world', None),
]
for query, expected in tests:
    result = extract_memory_candidate(query)
    if expected is None:
        assert result is None, f'{query}: expected None, got {result}'
    else:
        assert result == expected, f'{query}: expected {expected}, got {result}'
print('extract_memory_candidate tests passed')
"
```

- [ ] **Step 4: 提交**

无代码改动，无需提交。

---

### Task 10: 更新 TEST_CHECKLIST

**Files:**
- Modify: `TEST_CHECKLIST.md`

- [ ] **Step 1: 添加批次 2 验证清单**

```markdown
## 批次 2 验证 (2026-06-29)
- [ ] 上传文档后前端展示实时步骤（解析中 → 切块中 → 向量化 → 索引中 → 完成）
- [ ] DocumentList 不再触发 2s 轮询请求
- [ ] 输入"我叫XXX"后，正则提取 + LLM 确认 → 自动保存记忆
- [ ] 输入"我叫外卖"（噪音词）→ 不触发保存
- [ ] 记忆已保存后，重新问"我是谁"能召回
- [ ] `python -c "from rag.progress import progress"` 无 import 错误
- [ ] `python -c "from agent.intercept import extract_memory_candidate, confirm_memory"` 无 import 错误
- [ ] SSE 端点 `/api/documents/{doc_id}/progress` 可访问
```

- [ ] **Step 2: 提交**

```bash
git add TEST_CHECKLIST.md
git commit -m "docs: add batch 2 verification checklist"
```
