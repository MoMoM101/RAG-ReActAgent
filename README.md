# RAG Agent / 知识库智能助手

**[中文](#中文) | [English](#english)**

---

<h2 id="中文">中文</h2>

### 这是什么

RAG Agent 是一个**本地知识库 + 智能对话系统**。上传文档后直接用自然语言提问，Agent 会自动分析意图、检索相关段落、调用工具，最后基于原文给出回答。

它不是简单的"上传 PDF 聊天"，而是一个完整的 **ReAct Agent**：会判断你的问题属于哪类 —— 查文档？查记忆？联网搜？甚至会从对话中自动提取你的偏好和习惯存进长期记忆。

### 核心能力一览

**文档检索** — 支持 PDF / Word / Excel / 图片 / TXT，上传后全自动解析 → 切片 → 向量化 → 入库。整个过程在后台异步完成，前端实时显示进度。

**混合搜索** — 同时跑两路检索：

| 通路 | 引擎 | 擅长的 |
|------|------|--------|
| 语义搜索 | Qdrant | 自然语言问题、同义表述 |
| 关键词搜索 | BM25 (jieba 分词) | 精确代码、SKU、术语 |

两路结果经 **RRF 融合** + **Cross-Encoder 重排序** + **内容去重** + **质量过滤** 后送到 LLM。不是简单的"搜索+喂给GPT"。

**Agent 系统** — 真正模拟人的思考方式：

```
用户提问
  │
  ├─ 意图分类 → 规则优先（毫秒级）+ LLM 兜底
  ├─ 记忆拦截 → 你说"我决定用FastAPI"→ 自动存入长期记忆
  ├─ ReAct 循环 → 思考 → 调工具 → 看结果 → 再思考 → ……
  │    ├─ search_docs     检索知识库
  │    ├─ web_search      联网搜索（Bing→DuckDuckGo 双引擎）
  │    ├─ recall_memory   查找你的历史记忆
  │    ├─ calculator      数学计算
  │    └─ list_documents  查看已上传文档
  └─ 最终回答 → 基于检索结果，不编造
```

**全链路容错** — Agent 循环中如果上下文溢出，自动折半窗口重试。工具调用遇到网络超时自动指数退避。入库时 embedding token 超限自动缩小 chunk 重切。这些都是生产环境才会考虑的事情。

**用户记忆** — 两道防线确保精确：

1. **正则快速提取**：6 种模式覆盖"我叫/我是/我喜欢/我习惯/我决定/我项目"
2. **LLM 批量确认**：多条候选一次 API 调用判定是否值得保存

记忆按 `访问频次 × 0.3 + 最近时间 × 0.7` 加权评分，满容量自动淘汰低分项。

**前端** — SSE 流式渲染，你会看到 Agent 的**完整思考过程**：正在分析问题 → 思考中 → 调了什么工具、传了什么参数、拿到多少结果 → 逐字输出回答。

### 技术选型

| 层 | 技术 | 为什么选它 |
|------|------|------|
| 后端 | FastAPI + uvicorn | 原生异步，SSE 流式零成本接入 |
| Agent | 自研 ReAct Loop | 不依赖 LangChain 等重型框架，控制粒度细 |
| 向量库 | Qdrant | 本地模式零配置，gRPC 高性能，API 简洁 |
| 全文搜索 | BM25 (自研 SQLite 引擎) | jieba 中文分词 + IDF 统计，比 SQLite FTS5 的 trigram 准确得多 |
| 分块 | tiktoken + 段落优先 + 表格保护 | 200 token 粒度，按语义边界切分，不破坏表格 |
| LLM | OpenAI 兼容 API | 支持 OpenAI / DeepSeek / Qwen / 任意兼容服务 |
| Embedding | OpenAI 兼容 API | 同上 |
| 重排序 | Cross-Encoder (bge-reranker-v2-m3) | 开源模型，中文表现好，可本地/远端部署 |
| OCR | PaddleOCR | 中文识别率最高，三级级联降级（预处理→原始像素→跳过） |
| 前端 | React 19 + Vite + TailwindCSS | SSR 不适合这种纯交互场景，CSR+Vite 足够快 |
| 状态 | Zustand | 比 Redux 轻两个数量级，API 直觉化 |

### 快速开始

**Docker 一键部署**

```bash
git clone <repo-url> && cd RAG_Agent
cp backend/.env.example backend/.env   # 编辑填入 API Key
docker compose up -d
# → 前端 http://localhost:5173
# → 后端 http://localhost:8000
# → API 文档 http://localhost:8000/docs
```

**本地开发 — 三种安装模式**

```bash
# 最小安装：基础 RAG 功能
cd backend
pip install -r requirements.txt

# 完整安装：含重排序 + OCR + Web 搜索
pip install -r requirements.txt -r requirements-rerank.txt -r requirements-ocr.txt

# 开发安装：含测试 + 类型检查
pip install -r requirements.txt -r requirements-dev.txt
```

然后启动：

```bash
# 终端 1 — 后端
cd backend
python -m venv .venv && source .venv/bin/activate  # Windows: .venv\Scripts\activate
cp .env.example .env    # 填入 API Key
uvicorn main:app --host 0.0.0.0 --port 8000 --reload

# 终端 2 — 前端
cd frontend
npm install && npm run dev
```

首次启动时无需配置 API Key 也能正常运行——`/api/health/dependencies` 会显示各组件状态。

### 配置

所有配置项定义在 `backend/config.py`，通过 `.env` 覆盖。关键项：

| 变量 | 说明 | 默认 |
|------|------|------|
| `LLM_API_KEY` | 大模型 API 密钥 | 必填 |
| `LLM_MODEL` | 模型名 | `gpt-4o` |
| `LLM_BASE_URL` | API 地址 | `https://api.openai.com/v1` |
| `EMBEDDING_API_KEY` | 向量模型 API 密钥 | 必填 |
| `EMBEDDING_MODEL` | 向量模型名 | `text-embedding-3-small` |
| `CHUNK_SIZE` | 分块大小 | `200` |
| `RERANK_ENABLED` | 启用重排序 | `false` |
| `WEB_SEARCH_ENABLED` | 启用联网搜索 | `true` |
| `QUERY_REWRITE_ENABLED` | 启用查询改写（多路并行搜索） | `false` |
| `MEMORY_ENABLED` | 启用用户记忆 | `true` |
| `INGESTION_MAX_CONCURRENCY` | 入库并发数 | `3` |
| `RATE_LIMIT` | 聊天/上传 分别限流 | 30/min, 10/min |

API Key 支持 AES-256 加密存储（`ENC:` 前缀），首次启动自动生成随机 `SECRET_KEY`，防止泄露。

更多配置项见 **[配置文档](docs/CONFIGURATION.md)**。

### 文档索引

| 文档 | 内容 |
|------|------|
| [CONTRIBUTING.md](CONTRIBUTING.md) | 开发流程、命令速查、提交规范 |
| [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) | 模块图、RAG 时序、Agent 流程 |
| [docs/CONFIGURATION.md](docs/CONFIGURATION.md) | 全部环境变量说明 |
| [docs/OPEN_SOURCE_OPTIMIZATION_DESIGN.md](docs/OPEN_SOURCE_OPTIMIZATION_DESIGN.md) | 开源优化设计方案 |

### 检索架构

```
用户查询
  │
  ├─ [查询分类器] 自适应 RRF 权重
  │    ├─ 精确代码 → keyword 3.0, semantic 0.3
  │    ├─ 药品查询 → keyword 2.0, semantic 1.5
  │    └─ 自然语言 → semantic 3.0, keyword 0.5
  │
  ├─ [可选] 查询改写 → LLM 产出 2 个搜索变体
  │
  ├─ 并行检索 ─────────────────────────────────
  │  ├─ Qdrant 语义搜索 (n 个变体并行)
  │  └─ BM25 关键词搜索 (n 个变体并行)
  │     └─ 结果合并 → 按 chunk_id 去重
  │
  ├─ RRF 融合 → 加权 Reciprocal Rank Fusion
  ├─ 内容去重 → SequenceMatcher 跨文档去重
  ├─ 质量过滤 → 正则评分 → 可选 LLM 判分
  ├─ 重排序   → Cross-Encoder
  └─ Top-K 截断 → 送入 LLM 生成回答
```

评测数据（qrels v2, chunk_size=200, document_key/section_key 匹配）：

| 指标 | 数值 |
|------|:---:|
| Precision@5 | 参见最新 `evaluation_results.json` |
| Recall@5 | 参见最新 `evaluation_results.json` |
| MRR (首条命中排名) | 参见最新 `evaluation_results.json` |
| NDCG@5 | 参见最新 `evaluation_results.json` |
| Hit@5 (至少命中1条) | 参见最新 `evaluation_results.json` |

> 旧评测（Jaccard 文本匹配）已归档为 legacy，不再作为正式质量结论。正式评测使用 `eval_metrics.compute_metrics_v2()`。

### 项目演进

从 2026-06-24 到 07-09，在 17 天内完成 20+ 份设计文档、三轮大规模评审优化。关键节点：

| 日期 | 里程碑 |
|------|------|
| 06-24 | MVP 文档 RAG 闭环 + ReAct Agent Loop |
| 06-25 ~ 06-29 | Agent 工具化（5 工具 + 意图分类 + 记忆拦截 + 加密） |
| 07-01 ~ 07-02 | 核心稳定性（单例缓存、LLM 调用减半、限流、日志、并发控制） |
| 07-02 | Embedding 维度自动检测 + OCR 三级降级 + 测试 89→99 |
| 07-06 ~ 07-07 | RAG 优化（FTS5→BM25、chunk splitter 段落优先、消融评测） |
| 07-08 | chunk_size=200 验证 + Agent 全面优化（重试/tiktoken/模板化/并行测试） |
| 07-09 | 开源就绪（README + LICENSE + E2E + Docker CI + 查询改写 + 备份恢复） |

### 测试

```bash
cd backend
pytest tests/ -v              # 325+ 个单元/集成测试
python tests/evaluate_rag.py  # 检索精准度评测（含消融对比）[LEGACY]
python -m pytest tests/test_eval_metrics.py -v  # qrels v2 评测（正式入口）

cd frontend
npm test                      # 20 个组件测试
npm run test:e2e              # Playwright E2E（需要后端运行）
```

---

<h2 id="english">English</h2>

### What is this

RAG Agent is a **local knowledge base with an intelligent chat interface**. Upload documents, then ask questions in natural language. The Agent classifies your intent, retrieves relevant content, invokes tools, and answers based on sources — not from training data.

It is a full **ReAct Agent**: it thinks, calls tools, observes results, and iterates. It remembers your preferences across sessions. It falls back to web search when the knowledge base lacks information.

### Key Capabilities

**Document Ingestion** — PDF, Word, Excel, images, TXT. Fully automatic: parse → chunk → embed → index. Background processing with real-time progress.

**Hybrid Search** — Two retrieval paths running in parallel:

| Path | Engine | Best for |
|------|--------|----------|
| Semantic | Qdrant | Natural language, paraphrased queries |
| Keyword | BM25 (jieba tokenizer) | Exact codes, SKUs, terminology |

Results fused via **RRF** + **Cross-Encoder rerank** + **dedup** + **quality filter**.

**Agent System** — Human-like reasoning loop:

```
User query
  │
  ├─ Intent classification → rules first (milliseconds), LLM fallback
  ├─ Memory interception → auto-extracts preferences and facts
  ├─ ReAct loop → Think → Act → Observe → Think → …
  │    ├─ search_docs     knowledge base retrieval
  │    ├─ web_search      Bing → DuckDuckGo dual-engine fallback
  │    ├─ recall_memory   user profile lookup
  │    ├─ calculator      math evaluation (AST-based, safe)
  │    └─ list_documents  document inventory
  └─ Final answer → grounded in retrieved sources, no hallucination
```

**Resilience** — Context overflow auto-halves the window. Tool calls use exponential backoff on network errors. Failed ingestion retries with reduced chunk size. Stuck documents auto-clean after 30 minutes.

**User Memory** — Two-stage extraction: regex patterns catch explicit statements, then batch LLM confirmation filters noise. Weighted expiry (`access_count × 0.3 + recency × 0.7`) manages capacity.

**Streaming UI** — SSE streaming shows the full reasoning chain: intent detection → tool calls with arguments → result counts → incremental answer generation.

### Tech Stack

| Layer | Technology | Rationale |
|------|------|------|
| Backend | FastAPI + uvicorn | Native async, SSE streaming, OpenAPI docs |
| Agent | Custom ReAct Loop | No LangChain dependency, full control |
| Vector DB | Qdrant | Local mode, gRPC, clean API |
| Full-text | BM25 (custom SQLite engine) | jieba tokenization, proper IDF, beats FTS5 trigram |
| Chunking | tiktoken + paragraph-first | 200-token chunks, table boundary protection |
| LLM | OpenAI-compatible API | Any provider (OpenAI, DeepSeek, Qwen, local) |
| Embedding | OpenAI-compatible API | Same flexibility |
| Reranker | Cross-Encoder (bge-reranker-v2-m3) | Strong Chinese performance |
| OCR | PaddleOCR | Best Chinese recognition, triple cascade degradation |
| Frontend | React 19 + Vite + TailwindCSS | Fast dev cycles, lightweight |
| State | Zustand | Minimal boilerplate |

### Quick Start

```bash
git clone <repo-url> && cd RAG_Agent
cp backend/.env.example backend/.env   # add your API keys
docker compose up -d
# Frontend: http://localhost:5173
# Backend:  http://localhost:8000
# API docs: http://localhost:8000/docs
```

**Local install — three options:**

```bash
# Minimal: basic RAG
pip install -r backend/requirements.txt

# Full: rerank + OCR + web search
pip install -r backend/requirements.txt -r backend/requirements-rerank.txt -r backend/requirements-ocr.txt

# Dev: add tests + type checking
pip install -r backend/requirements.txt -r backend/requirements-dev.txt
```

See [CONFIGURATION.md](docs/CONFIGURATION.md) for all settings. The service starts without API keys — use `/api/health/dependencies` to check component status.

### Evaluation

Formal evaluation uses qrels v2 with stable `document_key`/`section_key` matching.
See `backend/tests/eval_metrics.py` → `compute_metrics_v2()` and `backend/tests/qrels_data_v2.json`.

Old Jaccard-based metrics (shown below) are archived as legacy:

| Metric | Value |
|------|:---:|
| P@5 | 59.2% |
| MRR | 97.3% |
| Hit@5 | 100% |
| Avg latency | 448ms |

### Testing

```bash
cd backend
pytest tests/ -v              # 325+ unit/integration tests
python tests/evaluate_rag.py  # precision evaluation with ablation

cd frontend
npm test                      # 20 component tests
npm run test:e2e              # Playwright E2E (requires running backend)
```

### License

MIT
