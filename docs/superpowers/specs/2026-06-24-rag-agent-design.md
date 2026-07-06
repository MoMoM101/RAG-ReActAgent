# ReAct RAG Agent — 完整设计文档

**日期**: 2026-06-24 | **状态**: 已确认 (rev2)

---

## 1. 项目概述

支持文档上传、自动切片、向量化、混合检索的 RAG 智能体，采用 ReAct 框架 + Hermes 风格 Function Calling，前后端分离架构。

**技术栈**: Python FastAPI + React + TypeScript + LlamaIndex + Qdrant + ChromaDB + SQLite

---

## 2. 分阶段路线图

### 第一阶段 MVP：文档 RAG 闭环

上传文档 → 解析切片 → 写入 SQLite + Qdrant + FTS5 → 混合检索 → SSE 聊天回答 + 来源引用。

**不做**: 长记忆、OCR 多引擎、web_search、calculator、reranker、相似度去重、设置中心。

### 第二阶段：Agent 工具化

加入 search_docs + calculator + list_documents 等内置工具，跑通 ReAct Function Calling loop、工具调用重试、超时、中断。

### 第三阶段：高级能力

长记忆、OCR、web_search、相似度去重、reranker、设置中心、多模型配置切换。

### 并行：工程底座

后台任务队列、入库状态机、日志追踪、配置加密、失败重试、基础 eval case（贯穿各阶段）。

---

## 3. 整体架构

```
┌─────────────────────────────────────────────┐
│                  前端 (React)                 │
│  聊天界面 · 文档管理 · 设置中心 · SSE 实时渲染  │
└─────────────────┬───────────────────────────┘
                  │ SSE + REST API
┌─────────────────┴───────────────────────────┐
│               后端 (FastAPI)                  │
│                                               │
│  ┌──────────┐  ┌──────────┐  ┌────────────┐  │
│  │ Agent 层  │  │  RAG 层  │  │  工具层     │  │
│  │ ReAct Loop│  │ LlamaIdx │  │ 可扩展工具  │  │
│  │ FC调度    │  │ 混合检索 │  │            │  │
│  │ 路由建议  │  │ 文档管线 │  │            │  │
│  └──────────┘  └──────────┘  └────────────┘  │
└───────┬──────────┬──────────┬────────────────┘
        │          │          │
   ┌────┴───┐ ┌───┴───┐ ┌───┴────┐
   │ Qdrant │ │ChromDB│ │ SQLite │
   │RAG文档  │ │长记忆 │ │元数据   │
   │向量检索 │ │(三期)  │ │FTS5 BM25│
   └────────┘ └───────┘ └────────┘
```

---

## 4. Agent 设计

### 4.1 意图分类：路由建议（非硬门禁）

分类器不作为阻塞关卡，而是产出 "路由建议" 注入 Agent 的 system prompt，Agent 自己决定是否采纳。

```
User Query + 上下文
        │
        ▼
┌──────────────────┐
│  Intent Classifier │  →  {intent, confidence, suggested_tools}
│  (轻量路由建议)    │     注入 system prompt 作为 hint
└──────┬───────────┘
       │
       ▼
┌──────────────────┐
│  ReAct Agent Loop │  自己决定先做什么、是否需要追问
│  (Function Call)  │
└──────────────────┘
```

- 分类器只给建议：`"用户可能在问文档内容，建议优先使用 search_docs"`，不加 `ambiguous` → 强制追问
- 追问由 Agent Loop 内部自然产生：信息不足时 LLM 自己输出 clarification 而非 final_answer
- 误分类不阻塞正确执行：Agent 可以忽略建议走自己的工具链

### 4.2 追问澄清

Agent 在 ReAct 循环中自行判断是否信息不足需要追问（不做成硬门禁）。触发条件：
- 指代不明 / 缺少关键参数 / 知识库为空

产生 `clarification` SSE 事件返回用户，等待回复后继续。

### 4.3 Function Calling 协议 (Hermes 风格)

```json
{
  "tool_call": {
    "name": "search_docs",
    "arguments": {"query": "部署流程", "top_k": 5}
  }
}
```

### 4.4 防护机制

| 限制 | 默认值 | 超限行为 |
|------|--------|----------|
| `max_loop_iterations` | 10 | 强制用已有信息生成回答 |
| `max_tool_retries` | 3 | 区分可重试(超时/限流)和不可重试(参数错误)，指数退避 |
| `max_total_time` | 120s | 中断返回部分结果 |
| `max_context_tokens` | 取决于LLM | 裁剪旧 ToolCall-Observation 对 |

---

## 5. SSE 事件流协议

### 5.1 事件类型

| event | 触发 | data payload |
|-------|------|--------------|
| `status` | Agent 状态变更 | `{"message":"正在检索文档...","tool":"search_docs"}` |
| `tool_call` | 开始调用工具 | `{"tool":"search_docs","args":{"query":"..."}}` |
| `tool_result` | 工具执行完成 | `{"tool":"search_docs","result_count":5,"duration_ms":320}` |
| `clarification` | 需要追问 | `{"question":"..."}` |
| `answer_chunk` | 流式最终回复 | `{"delta":"..."}` |
| `sources` | 引用来源 | `[{"doc":"...","chunk_idx":3,"text":"..."}]` |
| `error` | 执行出错 | `{"code":"...","message":"..."}` |
| `done` | 完成 | `{"conversation_id":"..."}` |

### 5.2 关于 thought 事件

不直接暴露 LLM 内部 raw thought 文本（可能含幻觉/不稳定推理）。用 `status` 事件替代，输出可解释的用户可见状态：

```
event: status  →  "正在检索知识库文档..."
event: status  →  "已找到 5 条相关内容，正在分析..."
event: status  →  "信息不足，需要向您确认..."
```

前端展示简洁的状态标签，不展示原始推理文本。

---

## 6. 文档入库：事务与状态机

### 6.1 入库步骤

```
① 接收文件 → 校验格式/大小
② 计算 SHA256 → 去重检查
③ 保存原始文件到 uploads/
④ 写入 SQLite 文档记录 (status=processing)
⑤ 解析文档 (Loader) → 切片 (Splitter)
⑥ 切片 Embedding → 批量写入 Qdrant
⑦ 切片文本 → 批量写入 SQLite FTS5
⑧ 更新状态 (status=ready)
```

### 6.2 状态机

```
uploaded → parsing → chunking → embedding → indexing → ready
                │          │          │           │
                └──────────┴──────────┴───────────┘
                         任何步骤失败 → failed
```

| 状态 | 说明 |
|------|------|
| `uploaded` | 文件已保存，等待处理 |
| `parsing` | 正在解析文档 |
| `chunking` | 正在切片 |
| `embedding` | 正在向量化 |
| `indexing` | 正在写入 FTS5 + Qdrant |
| `ready` | 入库完成 |
| `failed` | 处理失败，记录 error_message |

### 6.3 事务补偿策略

- **④ 之前失败**: 无需补偿，删除临时文件即可
- **⑤/⑥ 失败**: 删除已写入的 Qdrant 向量 + SQLite 记录，状态 → failed
- **⑦ 失败**: 回滚 Qdrant（按 chunk_ids 批量删除）+ SQLite 记录，状态 → failed
- **后台重试**: failed 状态的文档支持手动重新触发处理（因为可能是临时网络问题）
- **脏数据清理**: 定期检查 stuck 在中间状态超过 30 分钟的文档，自动标记 failed

### 6.4 删除时的联动清理

```
DELETE /api/documents/{id}
  → 查 SQLite 获取 chunk_ids
  → Qdrant delete by chunk_ids
  → SQLite FTS5 delete by chunk_ids
  → SQLite delete document record
  → 删除 uploads/ 原始文件
  → 返回成功或部分失败详情
```

### 6.5 SQLite FTS5 并发控制

FTS5 写入时对整表加排他锁。多个文档同时入库或入库与检索并发时会排队阻塞，严重时出现 database locked 错误。

应对措施：

- **开启 WAL 模式**: 启动时执行 `PRAGMA journal_mode=WAL`，将写操作串行写入 WAL 文件，读操作不阻塞写
- **入库写入串行化**: 后台 worker 单线程消费队列，避免并发写入 FTS5
- **超时设置**: 连接设置 `timeout=30` (秒)，写入等待超时后明确报错而非无限阻塞
- **检索读优先**: 检索查询走独立只读连接，不受入库写入锁影响 (WAL 模式下读不阻塞)

```python
# 初始化 SQLite
conn.execute("PRAGMA journal_mode=WAL")
conn.execute("PRAGMA busy_timeout=30000")
```

---

## 7. Embedding 版本管理

### 7.1 问题

模型名或维度变更后，旧向量与新模式不兼容，检索阶段维度不匹配会报错。

### 7.2 方案

每篇文档记录其 embedding 版本信息：

```sql
-- documents 表
embedding_model  TEXT,   -- e.g. "text-embedding-3-small"
embedding_dim    INT,    -- e.g. 1536
embedding_version TEXT   -- 用户自定义标签，如 "v1" / "v2"
```

Qdrant Collection 名带上版本后缀避免冲突：`rag_chunks_v1` / `rag_chunks_v2`。

### 7.3 模型变更流程

1. 用户修改 Embedding 配置 → 后端检测 model/dim 变化
2. 后端提示："检测到模型变更，旧文档向量需重建。是否立即重建？"
3. 用户确认后：新建 Collection → 重新 embedding 所有文档 → 切换检索目标到新 Collection → 删除旧 Collection
4. 未重建期间：旧文档标记 "embedding_outdated"，检索时跳过

### 7.4 检索时维度校验

检索前检查当前配置维度与 Qdrant Collection 维度是否一致。不一致时返回明确错误，提示用户重建。

---

## 8. 长记忆：用户可控

### 8.1 隐私与控制

- **长记忆可关闭**: Settings 中提供全局开关，关闭后不提取也不检索记忆
- **可查看**: 提供记忆列表页面，用户可浏览所有已存储的记忆条目
- **可删除**: 单条删除 + 全部清空
- **可编辑**: 用户可手动修正 LLM 误提取的记忆

### 8.2 记忆条目结构

```
{
  id, type (fact/preference/decision/qa_pair),
  content, source_conversation_id,
  created_at, last_accessed_at, access_count
}
```

### 8.3 质量约束

- 记忆提取只在会话结束后触发（非实时，不影响对话速度）
- 提取时带系统 prompt 约束："只提取明确的事实陈述和用户偏好，不要猜测或推断"
- 用户可标记某条记忆为"错误"，用于后续优化提取策略

---

## 9. 评测闭环

### 9.1 指标

| 指标 | 说明 | 计算方式 |
|------|------|----------|
| 检索命中率 (Hit@K) | Top-K 结果中包含正确答案的比例 | 标注数据集 |
| 引用准确率 | 回答中引用来源是否真实对应 | 人工/LLM 评估 |
| 幻觉率 | 回答中无来源支撑的断言比例 | LLM 逐句校验 |
| 回答有用率 | 用户对回答的满意度 | 前端 👍/👎 反馈 |

### 9.2 评测数据集

- 最少 20 条手工标注的 {query, relevant_chunks, expected_answer} 三元组
- 每次变更检索策略、切片参数后跑一遍
- 评测脚本落地 `backend/eval/`

### 9.3 前端反馈收集

每条 AI 回答下方提供 👍/👎 按钮，结果写入 SQLite，用于统计和后续优化。

---

## 10. RAG 检索设计

### 10.1 混合检索

```
Query → Embedding → Qdrant 语义检索 (top_k=10)  ─┐
                                                  ├→ RRF 融合 → 返回 top_k
Query → 分词    → SQLite FTS5 BM25   (top_k=10)  ─┘
```

### 10.2 切片策略

| 参数 | 默认值 |
|------|--------|
| 大小 | 512 tokens |
| 重叠 | 50 tokens (约10%) |
| 分隔符优先级 | 段落 → 句子 → 词 |
| 元数据 | 文档名 + chunk_idx + 页码 |

### 10.3 文档格式

| 格式 | 解析方式 |
|------|----------|
| .txt / .md | 直接读取 |
| .pdf | PyMuPDF (扫描版走 OCR) |
| .docx | python-docx |
| .csv / .xlsx | pandas → Markdown |
| .jpg / .png | OCR 工具 |

### 10.4 文件限制

- 单文件 50MB，批量最多 10 个
- 支持格式: pdf, docx, txt, md, csv, xlsx, jpg, png

### 10.5 去重

- **哈希去重 (MVP)**: SHA256 比对，完全一致直接拒绝
- **相似度去重 (三期)**: 内容 >95% 相似弹确认框

---

## 11. 记忆架构 (三期)

### 11.1 双层记忆

| 维度 | 短记忆 | 长记忆 |
|------|--------|--------|
| 范围 | 当前会话 | 跨会话 |
| 存储 | SQLite messages 表 | ChromaDB + SQLite |
| 检索 | 时间顺序 | 语义检索 + 时间衰减 |

### 11.2 存储隔离

| 用途 | 存储 | 阶段 |
|------|------|------|
| RAG 文档向量 | Qdrant `rag_chunks` | MVP |
| 长记忆向量 | ChromaDB `long_term_memory` | 三期 |
| BM25 全文索引 | SQLite FTS5 | MVP |
| 元数据 | SQLite | MVP |

---

## 12. Embedding 模型策略

RAG 和 Memory 各自独立配置，前端各一张配置卡片（三期实现完整设置中心；MVP 硬编码或简单配置文件）。

```
RAG Embedding:    Provider/Model/API Key/Dimensions
Memory Embedding: Provider/Model/API Key/Dimensions (三期)
```

---

## 13. OCR 工具 (三期完善)

独立工具，图片预处理管线 → 引擎识别 → 返回文本。默认 PaddleOCR，可切换 Tesseract/EasyOCR。

MVP 阶段：仅用 PyMuPDF 提取 PDF 文本，不覆盖扫描版 PDF 和图片。

---

## 14. 工具注册协议

```python
class BaseTool:
    name: str
    description: str       # LLM 可见
    parameters: dict       # JSON Schema
    max_retries: int = 3
    retry_backoff: float = 1.0

    async def execute(self, **kwargs) -> ToolResult: ...
```

### 内置工具（分阶段）

| 工具 | 阶段 | 说明 |
|------|------|------|
| `search_docs` | MVP | 混合检索 |
| `calculator` | 二期 | 数学计算 |
| `list_documents` | 二期 | 文档列表 |
| `get_document_info` | 二期 | 文档详情 |
| `web_search` | 三期 | 网络搜索 |
| `ocr_extract` | 三期 | OCR |
| `recall_memory` | 三期 | 长记忆检索 |

---

## 15. API 设计

| 模块 | 方法 | 路径 | 阶段 |
|------|------|------|------|
| 聊天 | POST | `/api/chat` | MVP |
| 会话 | GET/POST | `/api/conversations` | MVP |
| 会话 | DELETE | `/api/conversations/{id}` | MVP |
| 会话 | GET | `/api/conversations/{id}/messages` | MVP |
| 文档 | POST | `/api/documents/upload` | MVP |
| 文档 | GET | `/api/documents` | MVP |
| 文档 | DELETE | `/api/documents/{id}` | MVP |
| 文档 | GET | `/api/documents/{id}/chunks` | MVP |
| 文档 | POST | `/api/documents/{id}/reprocess` | MVP (failed 重试) |
| 配置 | GET/PUT | `/api/settings` | 三期 |
| 记忆 | GET/DELETE | `/api/memories` | 三期 |
| 反馈 | POST | `/api/feedback` | 三期 |

---

## 16. 后端模块结构

```
backend/
├── main.py
├── api/
│   ├── chat.py / documents.py / conversations.py / settings.py
├── agent/
│   ├── loop.py           # Agent ReAct 主循环
│   ├── classifier.py     # 意图路由建议（restricted → hint only）
│   ├── tools.py           # 工具注册中心
│   └── context.py         # 上下文窗口管理
├── rag/
│   ├── pipeline.py        # 文档入库管线
│   ├── loaders.py         # 文档加载器
│   ├── splitter.py        # 切片策略
│   ├── retriever.py       # 混合检索 (Qdrant + FTS5 + RRF)
│   └── reranker.py        # 重排序 (三期)
├── vectordb/
│   ├── base.py / qdrant.py / factory.py
├── textdb/
│   ├── base.py / sqlite_fts.py
├── embedding/
│   ├── base.py / openai.py / ollama.py / factory.py
├── llm/
│   ├── base.py / openai.py / claude.py / ollama.py
├── memory/
│   ├── short_term.py / long_term.py / extractor.py (二期/三期)
├── tools/
│   ├── web_search.py / calculator.py / ocr/ (按阶段)
├── models/
│   ├── schemas.py / database.py
├── storage/
│   └── files.py
├── eval/
│   ├── cases.json          # 评测用例
│   └── run_eval.py         # 评测脚本
└── worker/
    └── ingestion.py         # 后台入库任务队列 (并行工程底座)
```

---

## 17. 前端设计

### 17.1 技术栈

React 18 + TypeScript + Vite + React Router v6 + Zustand + Tailwind CSS + shadcn/ui

### 17.2 路由

| 路由 | 页面 | 阶段 |
|------|------|------|
| `/` | 聊天主页 | MVP |
| `/documents` | 文档管理 | MVP |
| `/settings` | 系统配置 | 三期 |

### 17.3 目录结构

```
frontend/src/
├── main.tsx / App.tsx
├── api/          # HTTP + SSE 封装
├── stores/       # Zustand: chatStore / documentStore / settingsStore
├── components/
│   ├── layout/   # Sidebar / MainLayout
│   ├── chat/     # ChatPanel / MessageList / MessageBubble
│   │             # ToolCallCard / SourceCard / ClarifyBubble / ChatInput
│   ├── documents/# DocumentList / DocumentCard / UploadZone / ChunkViewer
│   ├── settings/ # LLMConfig / EmbeddingConfig / OCRConfig (三期)
│   └── shared/   # Loading / ErrorBoundary
├── hooks/        # useSSE / useFileUpload
└── types/        # Message / Document / SSE 事件类型
```

### 17.4 SSE 连接状态机

```
idle → connecting → streaming → done
                  ↘ error
                  ↘ waiting_clarify → (用户回复) → streaming → done
```

### 17.5 关键交互

- **可解释状态**: 显示 "正在检索文档..." / "已找到 N 条相关内容" / "正在生成回答..."，不展示 raw thought
- **追问气泡**: Agent 主动追问时显示带 ❓ 的澄清气泡
- **中断操作**: 发送后显示停止按钮，可中断 Agent 执行
- **文件上传**: 拖拽 + 进度条 (上传 → 解析 → 切片 → 入库)
- **去重弹窗**: 哈希重复直接拒绝
- **删除确认**: 明确告知联动删除向量+全文索引
- **深色主题**: 默认暗色系

---

## 18. 工程底座（并行贯穿）

### 18.1 后台任务队列

文档入库是长任务，不走 HTTP 同步等待。上传 API 立即返回 `document_id + status=uploaded`，后台 worker 异步处理。

- 技术选型: 初期用 FastAPI BackgroundTasks，后续可切 Celery/RQ
- 前端轮询文档状态或通过 SSE 订阅处理进度

### 18.2 日志追踪

- 每次请求生成 `request_id` 贯穿 Agent 全程
- 结构化日志 (JSON 格式)，包含: timestamp / request_id / event / payload
- 本地文件 + 控制台双输出

### 18.3 配置安全

- API Key 在数据库中 AES 加密存储（密钥从环境变量读取）
- 前端获取配置时返回脱敏 API Key (`sk-****xxxx`)
- 环境变量: `SECRET_KEY` / `DATABASE_URL` / `QDRANT_URL`

### 18.4 失败重试

- 入库失败自动重试 1 次（延迟 10s）
- 仍失败则标记 `failed`，前端显示重试按钮
- Agent LLM 调用失败: 重试 2 次后退化为 "抱歉，服务暂时不可用"

---

## 19. 关键设计决策

| 决策点 | 选择 | 理由 |
|--------|------|------|
| Agent 框架 | 自研 + Hermes FC | 轻量可控，LLM 可替换 |
| 意图分类 | 路由建议（非硬门禁） | 避免瓶颈和误分类阻塞 |
| RAG 引擎 | LlamaIndex | 文档处理成熟 |
| 检索方式 | 混合检索 (BM25+向量+RRF) | 关键词+语义互补 |
| 向量库 | Qdrant (可替换) | 高性能，预留接口 |
| 全文索引 | SQLite FTS5 | 轻量零部署 |
| 长记忆存储 | ChromaDB (三期) | 嵌入式，与 RAG 物理隔离 |
| Embedding | RAG/Memory 分立，带版本管理 | 各自最优选型，可迁移 |
| 入库事务 | 状态机 + 补偿回滚 | 防止脏状态 |
| Thought 暴露 | 仅暴露 status，不暴露 raw thought | 安全 + 可解释 |
| 分阶段交付 | MVP → 二期 → 三期 | 快速闭环 + 逐步完善 |
| 前端 | React + TypeScript + Vite | 现代主流方案 |
