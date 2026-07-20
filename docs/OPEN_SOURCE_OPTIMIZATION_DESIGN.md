# RAG Agent 开源级优化设计

## 1. 背景与目标

当前项目已经具备完整的 RAG + Agent 产品雏形：支持文档入库、混合检索、ReAct 工具调用、SSE 流式输出、用户记忆、Docker 部署、CI、后端测试和前端交互界面。整体已达到“可展示、可继续迭代”的水平，但距离成熟开源项目仍存在一些会影响首次安装、稳定运行、检索质量和长期维护的问题。

本文档的目标是给出一套面向开源发布的优化设计，使项目达到以下状态：

- 新用户可以从干净环境按 README 一次性启动。
- 核心 RAG 检索链路有明确、可复现的质量保障。
- Agent 工具调用、聊天历史、错误降级具备可维护的工程边界。
- CI 能覆盖后端、前端、类型检查、构建和核心测试。
- 配置、依赖、密钥、数据目录符合开源项目的基本安全要求。

## 2. 当前优势

### 2.1 架构完整

项目已拆分为 `agent`、`rag`、`embedding`、`llm`、`vectordb`、`textdb`、`reranker`、`ocr`、`api`、`memory` 等模块，职责边界清晰。RAG 主链路具备语义检索、关键词检索、RRF 融合、可选重排、去重、质量过滤等能力。

### 2.2 Agent 能力较完整

Agent 已具备规则优先的意图分类、LLM fallback、工具注册、ReAct loop、上下文裁剪、工具重试、最终答案兜底和 SSE 事件输出。这些能力已经超过常见 MVP 项目。

### 2.3 工程化基础存在

项目已有 Dockerfile、docker-compose、GitHub Actions、pytest、mypy、ruff、前端单测和 Playwright E2E 雏形。说明项目具备向开源级演进的基础。

## 3. 核心问题清单

### P0：阻塞开源首次体验

1. README 引用了 `backend/.env.example`，但仓库中缺少该文件。
2. 后端 `requirements.txt` 未完整声明运行时依赖，例如 WebSearch fallback 使用的 `ddgs` / `duckduckgo_search` 以及 `bs4`。
3. 服务启动时主动调用 embedding API 检测维度，缺少 API key 或网络不可用时可能导致服务无法启动。
4. 本地 `.venv` 不应作为可信运行环境，开源用户必须能通过干净环境重建。

### P1：影响核心质量

1. BM25 批量入库时 `df` 统计存在偏差，可能影响 IDF 和关键词排序质量。
2. BM25 SQL 使用大量字符串拼接，虽然做了单引号转义，但维护性和安全边界不足。
3. 聊天历史持久化时，tool result 只保存摘要文本，没有保存真实检索结果，影响多轮 RAG 上下文连续性。
4. Agent 启动和后台任务大量使用 `asyncio.create_task`，缺少统一生命周期管理、异常收集和任务状态观测。

### P2：影响开源维护体验

1. CI 未覆盖前端 build、前端单测和 E2E。
2. `docker-compose.yml` 中前端 `VITE_API_BASE_URL=http://backend:8000` 可能不适合浏览器侧访问，应区分容器内地址和浏览器访问地址。
3. 配置项较多，但缺少分组后的 `.env.example`、配置说明和降级策略。
4. 缺少贡献指南、问题模板、发布清单和架构图。

## 4. 优化设计

## 4.1 配置与依赖治理

### 目标

让新用户在没有任何本地历史环境的情况下，可以按文档完成安装、配置、启动和测试。

### 设计

新增 `backend/.env.example`：

```env
# LLM
LLM_PROVIDER=openai
LLM_MODEL=gpt-4o
LLM_BASE_URL=https://api.openai.com/v1
LLM_API_KEY=

# Embedding
EMBEDDING_PROVIDER=openai
EMBEDDING_MODEL=text-embedding-3-small
EMBEDDING_BASE_URL=https://api.openai.com/v1
EMBEDDING_API_KEY=
EMBEDDING_DIM=1536

# Retrieval
CHUNK_SIZE=200
CHUNK_OVERLAP=40
RETRIEVAL_TOP_K=8
RERANK_ENABLED=false
QUERY_REWRITE_ENABLED=false

# Storage
DATABASE_URL=sqlite+aiosqlite:///./data/rag_agent.db
QDRANT_PATH=./data/qdrant
UPLOAD_DIR=./data/uploads

# Web Search
WEB_SEARCH_ENABLED=false
WEB_SEARCH_MAX_RESULTS=5
WEB_SEARCH_PROXY=

# Runtime
LOG_LEVEL=INFO
SECRET_KEY=
INGESTION_MAX_CONCURRENCY=3
```

依赖拆分建议：

- `requirements.txt`：最小可运行后端依赖。
- `requirements-dev.txt`：pytest、pytest-cov、mypy、ruff、httpx 等开发依赖。
- `requirements-ocr.txt`：PaddleOCR 相关重依赖。
- `requirements-rerank.txt`：sentence-transformers / torch 等重排模型依赖。
- `requirements-web.txt`：`beautifulsoup4`、`ddgs` 或 `duckduckgo_search`。

WebSearch 应遵循“依赖缺失可降级”：

- `WEB_SEARCH_ENABLED=false` 时不导入第三方搜索依赖。
- 开启后若依赖缺失，返回明确错误：`Web search dependency missing: pip install -r requirements-web.txt`。

### 验收标准

- 删除本地 `.venv` 后，按 README 创建虚拟环境并安装依赖可启动后端。
- `python -m pip install -r backend/requirements.txt` 后，基础文档上传、检索、聊天可运行。
- 可选功能缺依赖时不会影响基础服务启动。

## 4.2 启动流程降级设计

### 目标

后端服务启动不应强依赖外部 LLM / embedding API。健康检查、文档列表、配置页面等基础 API 应在无 key 状态下可用。

### 设计

将当前启动期 embedding 维度检测改为懒加载：

1. 启动时读取 `EMBEDDING_DIM` 作为默认维度。
2. 第一次执行 embedding 时检测真实维度。
3. 若 API 不可用，返回业务错误，不阻断 FastAPI lifespan。
4. `/api/health` 返回基础状态，新增 `/api/health/dependencies` 返回 LLM、embedding、Qdrant、SQLite 状态。

示例状态：

```json
{
  "status": "degraded",
  "dependencies": {
    "sqlite": "ok",
    "qdrant": "ok",
    "embedding": "missing_api_key",
    "llm": "missing_api_key"
  }
}
```

### 验收标准

- 未配置 API key 时，后端仍能启动，`/api/health` 返回 200。
- 调用需要模型的接口时返回明确错误。
- Docker Compose 首次启动不会因为缺 key 直接退出。

## 4.3 BM25 检索质量修复

### 目标

保证关键词检索的统计正确性，避免 `df` 低估导致 IDF 偏高，影响排序。

### 问题

`insert_batch` 中注释写的是按 unique `(term, chunk)` 更新 `df`，但当前实现对每个 term 只增加 1。若同一批次中 100 个 chunk 都包含同一 term，`df` 仍只增加 1。

### 设计

修复 `insert_batch` 的统计逻辑：

```python
for term, chunk_ids in all_terms.items():
    delta = len(chunk_ids)
    safe_term = term.replace("'", "''")
    stmts.append(
        f"INSERT INTO {self._stats} (term, df) VALUES ('{safe_term}', {delta}) "
        f"ON CONFLICT(term) DO UPDATE SET df = df + {delta}"
    )
```

同时补充测试：

- 单批次多个 chunk 共享 term，`df == chunk_count`。
- 删除 document 后，`df` 正确递减。
- 重复 reprocess 同一 document 后，`df` 不膨胀。

### 后续改进

将 SQL 拼接迁移到 SQLAlchemy 参数化执行，优先处理：

- `insert`
- `insert_batch`
- `search`
- `delete_by_document`
- `delete_by_chunks`

### 验收标准

- 新增 BM25 df 测试通过。
- RAG 评测中关键词型 query 不退化。
- ruff、mypy、pytest 全部通过。

## 4.4 Tool Result 持久化优化

### 目标

多轮对话中保留必要的工具调用上下文，避免下一轮只看到 `Success: n results` 而丢失检索内容。

### 设计

当前消息表已有 `tool_name`、`tool_call_id`、`tool_args`，建议增加或复用字段保存结构化 tool result：

方案 A：新增字段

- `tool_result_json TEXT`

方案 B：复用 `content`

- `content` 保存完整 JSON。
- 前端展示时按摘要渲染，历史重放时给 LLM 完整内容。

建议采用方案 A，更利于 UI 展示和 LLM 上下文控制。

持久化策略：

- `search_docs`：保存前 `top_k` 条检索结果，包含 `document_id`、`filename`、`text`、`score`。
- `web_search`：保存 title、url、snippet。
- `calculator`：保存 expression、result。
- `list_documents`：保存文档列表摘要。

上下文重放策略：

- 最近 1-2 轮工具结果保留完整内容。
- 更早工具结果压缩为摘要。
- 超过上下文预算时优先保留用户问题、最终答案和引用 sources。

### 验收标准

- 连续多轮追问时，第二轮能引用第一轮检索来源。
- OpenAI tool-call message chain 格式仍合法。
- 历史消息不会无限膨胀。

## 4.5 Agent 后台任务与异常治理

### 目标

避免后台任务静默失败，提升入库、记忆提取、上下文丢弃处理等异步流程的可观测性。

### 设计

新增统一任务管理器：

```python
class BackgroundTaskManager:
    def create(self, coro, name: str, metadata: dict | None = None) -> asyncio.Task:
        ...
```

能力：

- 统一 `create_task`。
- 捕获异常并记录结构化日志。
- 保存最近 N 个任务状态。
- 在 shutdown 时尝试 cancel 未完成任务。
- 暴露 `/api/settings/tasks` 或内部 debug 接口查看任务状态。

替换位置：

- 文档后台入库。
- 会话记忆提取。
- dropped history 记忆提取。
- reranker / OCR preload。

### 验收标准

- 后台任务异常不会静默丢失。
- 日志中包含任务名称、耗时、异常、关联 document_id / conversation_id。
- 服务关闭时没有明显 pending task warning。

## 4.6 CI/CD 完整化

### 目标

每次 PR 都验证后端、前端、类型、格式、构建和核心测试。

### 设计

将 CI 拆为四类 job：

1. `backend-quality`
   - `pip install -r requirements.txt -r requirements-dev.txt`
   - `ruff check`
   - `mypy`
   - `pytest --cov --cov-fail-under=60`

2. `frontend-quality`
   - `npm ci`
   - `npm run build`
   - `npm test`

3. `docker-build`
   - build backend image
   - build frontend image

4. `e2e`
   - 可选，仅在 main 或带 label 时运行
   - 使用 docker compose 启动服务
   - 运行 Playwright smoke test

### 验收标准

- PR 上后端和前端质量检查都必须通过。
- Docker build 在 PR 上至少构建不推送。
- main 分支 push 才推送镜像。

## 4.7 Docker 与前端访问修复

### 目标

确保 docker compose 启动后，浏览器可以正确访问后端 API。

### 问题

`VITE_API_BASE_URL=http://backend:8000` 是容器网络地址，浏览器侧通常无法解析 `backend`。

### 设计

前端容器有两种方案：

方案 A：前端直接访问宿主机映射端口

```env
VITE_API_BASE_URL=http://localhost:8000
```

方案 B：生产化 Nginx 反向代理

- 前端通过相对路径 `/api` 请求。
- Nginx 将 `/api` 转发到 `backend:8000`。
- 推荐用于正式开源部署。

短期建议采用方案 A，长期采用方案 B。

### 验收标准

- `docker compose up` 后访问 `http://localhost:5173` 可直接调用后端。
- README 明确本地开发和 Docker 模式的 API 地址差异。

## 4.8 开源文档补齐

### 目标

让用户、贡献者和维护者都能快速理解项目。

### 新增文档

- `CONTRIBUTING.md`：开发流程、测试命令、代码风格、提交规范。
- `docs/ARCHITECTURE.md`：模块图、RAG 时序图、Agent 时序图。
- `docs/CONFIGURATION.md`：所有环境变量说明。
- `docs/TROUBLESHOOTING.md`：常见错误，如 API key、Qdrant、OCR、Windows 编码、依赖安装。
- `.github/ISSUE_TEMPLATE/bug_report.yml`
- `.github/ISSUE_TEMPLATE/feature_request.yml`
- `.github/pull_request_template.md`

### README 调整

README 首页保留：

- 项目一句话定位。
- 截图或 GIF。
- 快速开始。
- 核心能力。
- 架构简图。
- 测试与开发命令。
- 文档索引。

把过长的演进记录、详细评测和内部优化计划移动到 `docs/`。

## 5. 实施路线

### Phase 1：开源可启动

优先级最高，目标是让外部用户能跑起来。

- 新增 `backend/.env.example`。
- 修正 README 中的启动命令。
- 补齐基础依赖和可选依赖说明。
- 启动流程支持无 key degraded mode。
- 修复 Docker 前端 API 地址。

### Phase 2：核心正确性

目标是修复会影响检索和多轮对话质量的问题。

- 修复 BM25 `insert_batch` 的 df 统计。
- 增加 BM25 统计测试。
- 持久化完整 tool result。
- 增加多轮追问测试。
- 梳理 Agent create_task 异常处理。

### Phase 3：工程质量

目标是让维护成本下降。

- SQL 参数化迁移。
- 引入后台任务管理器。
- CI 增加前端 build/test。
- 增加 Docker build PR 检查。
- 补齐贡献文档和 issue 模板。

### Phase 4：可观测与评测

目标是从“能跑”走向“可证明有效”。

- 固化 RAG eval 数据集。
- 输出 P@K、MRR、Hit@K、延迟指标。
- 将评测脚本接入手动 CI workflow。
- 增加结构化日志字段。
- 增加检索 trace 导出能力。

## 6. 风险与权衡

### 6.1 可选依赖拆分会增加文档复杂度

OCR、reranker、web search 都可能引入较重依赖。拆分 extras 后，用户需要理解功能与依赖的关系。解决方式是在 README 中给出三种安装模式：

- minimal：只跑基础 RAG。
- full：启用 web search、rerank、OCR。
- dev：用于贡献代码和跑测试。

### 6.2 Tool result 全量保存会增加数据库体积

检索结果文本较长，长期保存会增大 SQLite 文件。解决方式：

- 单次工具结果限制最大字符数。
- 只保存最近 N 轮完整结果。
- 老结果定期压缩为摘要。

### 6.3 启动懒加载会推迟错误暴露

无 key 时服务能启动，但用户第一次调用模型接口才看到错误。解决方式：

- 设置页显示依赖状态。
- `/api/health/dependencies` 明确展示 missing key。
- 前端在聊天页显示配置提醒。

## 7. 开源级验收清单

发布前建议逐项确认：

- [ ] `backend/.env.example` 存在且 README 命令可执行。
- [ ] 干净 Python 3.12 环境安装依赖后可启动。
- [ ] 无 API key 时后端可启动并返回 degraded health。
- [ ] Docker Compose 启动后前端可访问后端。
- [ ] 后端 pytest、ruff、mypy 通过。
- [ ] 前端 `npm run build`、`npm test` 通过。
- [ ] BM25 df 统计测试通过。
- [ ] 多轮工具调用历史恢复测试通过。
- [ ] `.env`、data、uploads、`.venv` 均未被 git 跟踪。
- [ ] README 不引用不存在的文件或命令。
- [ ] 贡献指南、配置说明、故障排查文档齐全。

## 8. 建议结论

项目目前适合定位为“功能完整的 RAG Agent Beta 项目”。完成 Phase 1 和 Phase 2 后，可以达到较好的个人开源项目水平；完成 Phase 3 和 Phase 4 后，才更接近成熟开源项目。

最关键的原则是：先让陌生用户能无痛跑起来，再证明核心检索质量可靠，最后再追求更完整的工程治理。
