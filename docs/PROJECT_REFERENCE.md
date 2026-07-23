# RAG Agent 项目参考

> 面向版本：`v0.1.0-beta` 开源候选  
> 内容整理日期：2026-07-22  
> 本文将原 `docs/` 中的架构、配置、评测、运维与历史设计结论压缩为一个公开参考入口。代码、`backend/.env.example`、数据库迁移和自动化测试始终是行为事实的最终来源。

## 目录

- [1. 项目范围与发布状态](#1-项目范围与发布状态)
- [2. 系统架构](#2-系统架构)
- [3. 核心运行链路](#3-核心运行链路)
- [4. 安装与部署](#4-安装与部署)
- [5. 配置参考](#5-配置参考)
- [6. 数据、安全与隐私边界](#6-数据安全与隐私边界)
- [7. 数据库迁移、备份与恢复](#7-数据库迁移备份与恢复)
- [8. 质量评测与发布门禁](#8-质量评测与发布门禁)
- [9. 开发与测试](#9-开发与测试)
- [10. 故障排查](#10-故障排查)
- [11. 关键设计结论](#11-关键设计结论)
- [12. 已知限制与后续方向](#12-已知限制与后续方向)

## 1. 项目范围与发布状态

RAG Agent 是一个本地知识库与智能对话系统。用户上传文档后，系统在后台完成解析、切片、向量化和双索引写入；聊天 Agent 根据问题选择知识库检索、联网搜索、记忆、计算器和文档工具，并以 SSE 返回带来源的回答。

当前发布定位：

- 单租户、单实例 SQLite 写入；
- 支持本地运行或 Docker Compose 部署；
- 支持 PDF、Word、Excel、CSV、TXT、Markdown 和图片等输入；
- 使用 Qdrant 语义检索与 SQLite BM25 关键词检索；
- 支持可选 OCR、Cross-Encoder Reranker 和 Web Search；
- 当前是开源发布候选，尚未形成正式标签。

首版明确不承诺：

- 多租户数据隔离；
- 多实例并发写入和高可用；
- Kubernetes、企业级 SSO/OIDC；
- 对不受信任公网环境的默认安全部署；
- 所有模型、代理和操作系统组合均得到官方支持。

### 当前验证快照

以下结果来自 2026-07-23 的当前工作区验证，只证明本次候选代码；正式标签前仍应在最终提交上由 GitHub CI 再次执行：

| 门禁 | 结果 |
|---|---:|
| 后端完整离线回归 | 920 passed / 11 skipped / 15 deselected |
| Ruff | 通过 |
| MyPy（243 个源文件） | 通过 |
| 前端 Vitest | 64 passed（9 files） |
| 前端 Oxlint | 通过 |
| 前端生产构建 | 通过 |
| Grounded-answer release gate | passed / 0 blockers |
| pip-audit 基础依赖审计 | 0 个已知漏洞 |
| npm 生产依赖审计 | 0 个已知漏洞 |
| Docker E2E | 12/12 stages passed |
| Docker 严格冒烟测试 | 5/5 passed |

复杂混淆文档离线集的当前记录为 Recall@5 93.0%、Hit@5 100%、MRR 97.7%、NDCG@5 93.2%。这些指标必须与数据集、评测器和发布提交指纹一起解读，不能脱离 provenance 单独宣传。

## 2. 系统架构

```text
main.py                         统一启动器：后端、前端、就绪检查与子进程监控
backend/
├── main.py                     FastAPI 应用、生命周期和路由装配
├── config.py                   pydantic-settings 配置入口
├── api/                        文档、聊天、会话、设置、用户、审计、备份 API
├── auth/                       JWT、密码校验与角色信息
├── agent/                      ReAct 循环、上下文、工具与回答校验
├── rag/                        加载、切片、检索、分类与查询消歧
├── embedding/                  OpenAI 兼容 Embedding 抽象
├── llm/                        OpenAI 兼容 LLM 抽象
├── textdb/                     SQLite BM25 关键词索引
├── vectordb/                   Qdrant 本地或远端向量索引
├── reranker/                   可选 Cross-Encoder 精排
├── ocr/                        可选 OCR 与降级策略
├── memory/                     用户画像、记忆去重与提取
├── models/                     SQLAlchemy ORM 与数据库初始化
├── alembic/                    数据库版本迁移
├── storage/                    文件与存储辅助逻辑
└── worker/                     可恢复的持久后台任务
frontend/
├── src/api/                    HTTP/SSE 客户端
├── src/components/             React 页面与组件
├── src/stores/                 Zustand 状态
├── src/types/                  前后端数据契约
└── e2e/                        Playwright 场景
scripts/                        Docker 验收、备份、Schema 与性能基准脚本
```

关键技术：FastAPI、SQLAlchemy、Alembic、SQLite、Qdrant、React 19、Vite、Zustand、tiktoken、jieba，以及可选 PaddleOCR/DocTR 和 sentence-transformers。

## 3. 核心运行链路

### 3.1 文档入库

```text
上传文件
  → 流式落盘与容量检查
  → SHA-256 重复检测
  → 格式解析与可选 OCR
  → 段落优先、表格保护的 tokenizer 切片
  → 批量 Embedding
  → Qdrant 向量索引 + SQLite BM25 索引
  → 文档状态和前端进度收敛
```

- 单文件默认上限 200 MB，合法范围 1–512 MB；批量默认最多 50 个文件、总计 1024 MB。
- 后端按流式方式落盘，前端先读取服务端限制并提前拦截超限文件。
- 超大扫描 PDF 的实际成本还包括 OCR、切片、Embedding 和索引；512 MB 是硬边界，不是推荐尺寸。
- 入库任务持久化状态和心跳。服务重启后，超时或中断任务可恢复或进入明确失败状态。
- 删除、重建和恢复操作需要同时维护文件、SQLite、Qdrant 和 BM25 的一致性。

### 3.2 混合检索与消歧

```text
用户查询
  → 查询语义分类与自适应权重
  → 提取代码、实体、数值、单位等消歧信号
  → 可选查询改写
  → Qdrant 语义检索 ─┐
  → BM25 关键词检索 ─┴→ RRF 融合
  → 消歧奖励、内容去重和质量过滤
  → 可选 Cross-Encoder Rerank
  → Top-K 证据
```

设计重点：

- 精确代码、SKU 和专有名词提高关键词权重；自然语言问题提高语义权重。
- 查询中的货币、数值、时间、型号和实体名属于强消歧信号，可对候选结果加权。
- 短查询和跨文档术语重叠是主要歧义来源；不能只依赖向量相似度。
- Reranker 未就绪时直接使用 RRF 结果，不阻塞核心检索。
- 评测必须隔离 collection 和文档范围，防止共享知识库污染结果。

### 3.3 Agent、引用与 SSE

```text
用户消息
  → 意图分类（规则优先，LLM 兜底）
  → 当前轮记忆识别
  → tokenizer 感知的上下文预算
  → ReAct 循环：思考 → 工具 → 结果 → 再思考
  → 基于证据生成回答
  → 引用和事实覆盖校验
  → SSE 输出与会话持久化
```

主要工具包括：

- `search_docs`：混合检索知识库；
- `web_search`：可选联网搜索；
- `calculator`：AST 白名单计算；
- `list_documents` / `get_document_info`：文档信息；
- `recall_memory`：读取用户长期记忆。

检索结果按不可信外部内容处理，进入上下文时保留隔离标签。工具调用与工具结果作为原子消息组参与裁剪，避免生成不合法的 LLM 工具消息链。

### 3.4 长上下文与记忆

- 系统提示、工具定义、工具参数、历史消息和回答预留统一进入 token 预算。
- 超限时从旧到新裁剪完整消息组，优先保留系统提示和最新用户消息。
- 工具结果先按 tokenizer 安全截断，隔离标签必须保持闭合。
- 被裁剪消息生成有界工作摘要，写回会话行，不创建无限增长的摘要日志。
- 摘要使用来源消息指纹和乐观锁避免重复归纳与并发覆盖。
- 用户画像执行精确匹配、Embedding 相似度和字符串相似度三级去重。
- 记忆提取失败不推进水位；成功得到空结果也推进，避免重复调用。

### 3.5 可恢复后台任务

文档入库、上下文归纳和会话记忆提取等任务持久化以下信息：任务类型、JSON payload、执行次数、心跳、重试时间和最终状态。异常任务进入 `retry_wait`，超过次数进入 `dead_letter`；终态记录有数量上限，等待和运行中的任务不参与清理。

### 3.6 OCR / Reranker 可选模型

```text
disabled → downloading → loading → ready
                         └→ failed / missing_dependency
```

- 180 秒是核心服务就绪等待和用户提示阈值，不是模型下载总时限。
- Reranker 未就绪时保留 Qdrant + BM25 + RRF 顺序。
- OCR 未就绪时，需要 OCR 的文档进入 `waiting_for_ocr`；模型就绪后自动恢复入库。
- 模型缓存支持重启复用；可选模型失败不会把核心 `/api/health` 变为不可用。
- Docker 基础镜像默认不要求 OCR 模型；正式部署建议挂载持久模型缓存卷。

手动预下载：

```powershell
cd backend
..\.venv\Scripts\python.exe -m tools.download_models --ocr --reranker
```

## 4. 安装与部署

### 4.1 Docker Compose

```bash
git clone <repo-url>
cd RAG_Agent
cp backend/.env.example backend/.env
# 编辑 backend/.env，至少设置 JWT_SECRET（32+ 字符）和
# BOOTSTRAP_ADMIN_PASSWORD（12+ 字符）；模型凭据可稍后配置
docker compose --env-file backend/.env up -d
```

默认入口：

- 前端：`http://localhost:5173`
- 后端：`http://localhost:8000`
- OpenAPI：`http://localhost:8000/docs`
- 核心健康：`GET /api/health`
- 依赖状态：`GET /api/health/dependencies`

Compose 包含 Qdrant、后端和 Nginx 前端，并使用命名卷保存 Qdrant 与后端数据。浏览器经前端 `/api` 反向代理访问后端，不使用只能在容器网络中解析的 `backend` 主机名。

### 4.2 本地开发

源码方式开发需要 Python 3.12 和 Node.js 22，不要求安装 Docker；默认可使用 Qdrant 本地模式。只有选择 Docker Compose 部署，或执行 Docker 端到端验收测试时，才需要 Docker。

```bash
# 后端
cd backend
python -m venv .venv
# Linux/macOS: source .venv/bin/activate
# Windows: .venv\Scripts\activate
pip install -r requirements.txt -r requirements-dev.txt
cp .env.example .env
# 设置 JWT_SECRET 和 BOOTSTRAP_ADMIN_PASSWORD
uvicorn main:app --reload

# 前端（另一个终端）
cd frontend
npm ci
npm run dev
```

可选能力：

```bash
pip install -r backend/requirements-rerank.txt
pip install -r backend/requirements-ocr.txt
```

### 4.3 反向代理与容量

反向代理的请求体限制必须不小于应用上传限制，并预留 multipart 开销。应用上限为 200 MB 时，Nginx 可采用：

```nginx
client_max_body_size 201m;
proxy_read_timeout 300s;
proxy_send_timeout 300s;
```

生产环境必须自行配置 HTTPS、网络访问控制、持久卷权限、日志轮转和备份策略。

## 5. 配置参考

所有配置由 `backend/config.py` 定义，通过 `backend/.env` 覆盖。复制 `backend/.env.example` 后修改；该示例文件是公开配置名称和默认值的权威清单。

### 5.1 模型与检索

| 分组 | 主要变量 | 说明 |
|---|---|---|
| LLM | `LLM_PROVIDER`、`LLM_MODEL`、`LLM_BASE_URL`、`LLM_API_KEY` | OpenAI 兼容聊天模型 |
| Embedding | `EMBEDDING_MODEL`、`EMBEDDING_BASE_URL`、`EMBEDDING_API_KEY`、`EMBEDDING_DIM` | 维度必须与现有 Qdrant collection 一致 |
| Qdrant | `QDRANT_HOST`、`QDRANT_PORT`、`QDRANT_PATH`、`QDRANT_COLLECTION` | Host 为空时使用本地模式 |
| Chunk | `CHUNK_SIZE`、`CHUNK_OVERLAP` | 默认 200 / 40 token |
| Retrieval | `RETRIEVAL_TOP_K`、`RRF_*`、`DEDUP_*`、`QUERY_REWRITE_*` | 融合、去重与查询改写 |
| Rerank | `RERANK_ENABLED`、`RERANK_MODEL`、`RERANK_TOP_N` | 可选本地模型 |
| Grounding | `GROUNDING_*`、`RAG_GENERATION_MAX_TOKENS` | 引用校验、修复与回答预算 |

### 5.2 上下文、记忆与任务

| 变量 | 用途 |
|---|---|
| `LLM_OUTPUT_TOKEN_RESERVE` / `LLM_REASONING_TOKEN_RESERVE` | 回答与推理预留 |
| `CONTEXT_SAFETY_TOKENS` | 上下文安全余量 |
| `CONTEXT_TOOL_RESULT_MAX_TOKENS` | 单个工具结果上限 |
| `CONTEXT_SUMMARY_MAX_TOKENS` / `CONTEXT_SUMMARY_MAX_ITEMS` | 持久工作摘要边界 |
| `MEMORY_EXTRACT_MAX_TOKENS` | 会话记忆提取输入上限 |
| `MEMORY_EXTRACT_DEBOUNCE_SECONDS` | 会话提取防抖 |
| `TOKENIZER_PROVIDER` / `TOKENIZER_MODEL` | 模型感知 tokenizer |
| `TOKENIZER_FALLBACK_SAFETY_FACTOR` | 离线估算安全系数 |
| `INGESTION_MAX_CONCURRENCY` / `INGESTION_MAX_RETRIES` | 入库并发与重试 |

### 5.3 文件、模型和服务

| 变量 | 默认或用途 |
|---|---|
| `DATABASE_URL` | SQLite 数据库 |
| `UPLOAD_DIR` | 上传文件目录 |
| `DOCUMENT_MAX_UPLOAD_MB` | 默认 200，范围 1–512 MB |
| `DOCUMENT_BATCH_MAX_FILES` | 默认 50 |
| `DOCUMENT_BATCH_MAX_TOTAL_MB` | 默认 1024 MB |
| `OCR_ENABLED` | 是否启用 OCR |
| `OPTIONAL_MODEL_NOTICE_SECONDS` | 默认 180 秒提示阈值 |
| `OPTIONAL_MODEL_POLL_SECONDS` | 默认 5 秒状态轮询 |
| `WEB_SEARCH_ENABLED` | 默认关闭 |
| `SERVER_HOST` | 默认 `127.0.0.1` |
| `ALLOW_REMOTE_ACCESS` | 默认 `false` |
| `LOG_LEVEL` | 日志级别 |

### 5.4 安全配置

| 变量 | 要求 |
|---|---|
| `SECRET_KEY` | 用于敏感配置加密；部署时固定并妥善保存 |
| `JWT_SECRET` | JWT 签名密钥；必须固定且至少 32 字符 |
| `BOOTSTRAP_ADMIN_USERNAME` | 空数据库首次启动时创建的管理员用户名，默认 `admin` |
| `BOOTSTRAP_ADMIN_PASSWORD` | 空数据库首次启动时创建管理员所需；至少 12 字符，且 bcrypt 限制为 72 UTF-8 字节以内 |
| `JWT_ACCESS_TOKEN_EXPIRE_MINUTES` | Access Token 有效期 |
| `JWT_REFRESH_TOKEN_EXPIRE_DAYS` | Refresh Token 有效期 |
| `AUTH_COOKIE_SECURE` | 本地 HTTP 为 `false`；HTTPS 部署必须为 `true` |

不要提交真实 `.env`。不得把掩码后的 `***` 值复制回配置。密钥一旦进入 Git 历史，删除当前文件不能消除泄漏，必须轮换并清理历史。

## 6. 数据、安全与隐私边界

### 6.1 数据位置

默认本地数据包括：

- SQLite：用户、会话、消息、文档状态、审计、持久任务和配置元数据；
- Qdrant：文档向量与检索 payload；
- BM25：关键词索引；
- Upload 目录：原始上传文件；
- 模型缓存：OCR/Reranker 下载内容；
- 日志和备份：可能包含路径、对象 ID 和错误上下文。

删除单个目录不一定等于完整删除一个文档。对外提供数据删除能力时，必须同时覆盖原文件、SQLite、Qdrant、BM25、缓存和备份保留策略。

### 6.2 网络与第三方服务

根据配置，系统可能访问：

- LLM 和 Embedding API；
- Web Search 服务；
- Hugging Face 或 OCR 模型下载地址；
- 用户配置的 Qdrant 远端服务。

使用外部模型意味着问题、检索上下文或文档片段可能发送给相应供应商。部署者负责阅读供应商条款并确定数据是否允许离开本地环境。

### 6.3 身份与访问控制

- 业务 API 使用短期 JWT Bearer 鉴权；Refresh Token 仅存放在 `HttpOnly + SameSite=Lax` Cookie 中，前端 JavaScript 不可读取。
- Refresh Cookie 默认保留 7 天，重新打开页面时会自动续签 Access Token；退出登录会由后端清除 Cookie。
- 首次管理员只在用户表为空时创建；后续启动不会用环境变量覆盖现有账号或密码。
- 登录页提供可选的“修改密码”入口，不强制用户修改历史默认密码。
- 修改密码要求验证当前密码；新密码不限制长度或字符类型，修改后旧 Refresh Token 立即失效。
- 默认绑定 `127.0.0.1` 且不允许远程访问。
- 当前存在用户和角色基础，但不等于完整多租户隔离。
- 不应把管理员权限、工具调用权限或恢复接口授予不受信任用户。
- 公网部署必须增加 HTTPS、防火墙、反向代理限制和独立强密钥。

### 6.4 RAG/Agent 特有风险

- 上传文档和 Web Search 结果均属于不可信内容，可能包含提示注入。
- 引用校验降低幻觉风险，但不保证事实绝对正确。
- 文档解析器、压缩包恢复、OCR 和模型文件属于高风险输入面，应保持依赖更新。
- 备份恢复必须限制成员数量、单文件大小、解压后总大小和路径穿越。
- Calculator 仅允许数字、基本算术和安全 AST 节点，不能替换为直接 `eval`。

## 7. 数据库迁移、备份与恢复

### 7.1 Alembic 迁移

当前上下文快照和可恢复任务相关结构对应 Alembic revision `0003`。升级已有数据库前先备份，然后执行：

```powershell
cd backend
..\.venv\Scripts\python.exe -m alembic upgrade head
```

- 根 `main.py` 的单机启动模式可以在后端就绪前执行向前迁移。
- 直接运行 Uvicorn 时不应假定自动迁移已启用。
- 已有表但没有 Alembic revision 的数据库不得直接 `stamp head` 掩盖缺失字段。
- SQLite 升级前使用 backup API 创建一致性快照，并执行 `PRAGMA quick_check`。
- 迁移备份默认仅保留最近 5 份，由 `MIGRATION_BACKUP_KEEP` 调整。

### 7.2 备份与恢复

备份恢复覆盖 SQLite、上传文件、向量索引状态和必要 manifest。恢复时：

1. 验证归档格式、路径、成员数和容量；
2. 进入维护状态，暂停新任务并等待受控任务结束；
3. 创建当前 SQLite 一致性回滚副本；
4. 校验恢复内容和版本；
5. 原子切换或在 Windows 文件占用时执行一致性回灌；
6. 执行数据库与索引一致性检查；
7. 失败时使用同一安全切换路径回滚；
8. 从恢复后的数据库重新加载持久任务。

不要把备份文件提交到 Git。正式部署应把备份复制到独立故障域，并定期做真实恢复演练。

## 8. 质量评测与发布门禁

### 8.1 检索评测

正式检索评测使用稳定的 `document_key` / `section_key` qrels，指标包括 Precision@K、Recall@K、Hit@K、MRR 和 NDCG@K。历史 Jaccard 文本匹配曾出现 Recall 超过 100%、标注错位和共享 collection 污染，已不作为发布质量结论。

评测要求：

- 固定数据集和 qrels 指纹；
- 每次运行使用隔离索引或严格文档范围；
- 禁止用运行结果自动回填 ground truth；
- 报告区分普通、业务、复杂消歧和在线问答范围；
- Reranker 对比必须同时报告质量收益与延迟成本；
- 历史报告不能覆盖当前正式报告。

### 8.2 Grounded Answer 评测

在线问答评测关注：事实召回、回答完成度、引用精确率、引用完整率、拒答准确率、首 token 延迟、总耗时和修复调用比例。报告保存数据集、校验器、评测器和提示词 SHA-256；任一输入变化都会使旧报告失去当前版本证明力。

### 8.3 两级门禁

日常 CI：

- Ruff、MyPy；
- 后端测试和覆盖率；
- 前端 lint、测试和生产构建；
- 备份安全测试；
- Backend Docker 和完整 Compose 冒烟。

正式发布门禁：

```powershell
cd backend
python release_gate.py
```

门禁检查完整性、报告时间、provenance、质量阈值和性能阈值，任何 blocker 都必须返回非零退出码。

重新评分只能更新评分器相关结果，不能伪装成新的模型生成。代码、数据集、校验器或提示词变化后，应重新运行完整在线评测。

## 9. 开发与测试

### 9.1 后端

```bash
cd backend
pytest tests/ -v
pytest tests/ -v -m "not docker" --cov=. --cov-config=.coveragerc --cov-report=term-missing
ruff check . --config ../pyproject.toml
python -m mypy . --config-file ../pyproject.toml
```

正式 qrels 指标和 Release Gate 使用仓库内对应测试脚本；旧的 Jaccard 评测仅用于历史兼容，不应写入 README 当前指标。

### 9.2 前端

```bash
cd frontend
npm ci
npm run lint
npm test
npm run build
```

### 9.3 Docker 验收

```powershell
./scripts/docker_e2e_acceptance.ps1 -Clean
```

验收覆盖镜像构建、健康检查、鉴权、上传、双索引、SSE、重启持久化、备份恢复和 Qdrant 降级恢复。执行后应清理测试容器、卷和本地报告。

## 10. 故障排查

### 10.1 服务启动但模型调用失败

检查：

```text
GET /api/health/dependencies
```

- `missing_api_key`：检查 `LLM_API_KEY` / `EMBEDDING_API_KEY`；
- 连接拒绝或超时：检查 Base URL、代理和供应商兼容性；
- 不要把前端返回的掩码密钥写回 `.env`。

### 10.2 Qdrant 维度不匹配

Embedding 模型维度与现有 collection 不一致时，需要重建索引。先备份并确认所有原始文档仍可访问，再调用受保护的重建接口。切换 Embedding 模型不能只修改模型名。

### 10.3 文档长时间停在处理中

- 查看依赖状态和后台任务状态；
- OCR 文档可能处于 `waiting_for_ocr`，不是失败；
- 检查 `data/logs`、磁盘空间、上传目录权限和外部 Embedding 状态；
- 重启后仍失败的任务应显示重试或 dead-letter 状态，而不是无限 pending。

### 10.4 OCR / Reranker 失败

```bash
pip install -r backend/requirements-ocr.txt
pip install -r backend/requirements-rerank.txt
```

不需要相关能力时关闭对应开关。模型下载中断后保留缓存，再次启动或手动下载可继续。

### 10.5 前端无法访问 API

- Docker 模式通过 Nginx 的相对 `/api` 代理；
- 浏览器不能访问容器内部的 `http://backend:8000`；
- 检查后端 `/api/health`、前端代理配置和鉴权 Token；
- 构建问题优先使用 `npm ci` 与锁文件恢复，不使用来源不明的全局包。

### 10.6 Windows 编码或 SQLite 文件占用

- 终端和文件使用 UTF-8；PowerShell 可设置 UTF-8 输出编码；
- 杀毒软件和索引服务可能短暂占用 SQLite/Qdrant 文件；
- 恢复逻辑有 Windows 一致性回灌兜底，但仍应避免把数据目录放在不稳定的同步盘中。

提交 Issue 时提供操作系统、Python/Node 版本、脱敏配置、可复现步骤、相关日志和完整堆栈，不要粘贴 API Key 或原始用户文档。

## 11. 关键设计结论

原 105 份文档中反复验证、对当前实现仍有参考价值的结论如下：

1. **先保证陌生环境可启动，再优化高级能力。** 无 API Key 和无可选模型时，核心服务应以 degraded 状态启动。
2. **RAG 质量不能用单一指标证明。** Precision、Recall、MRR、NDCG、Hit Rate、延迟和拒答必须结合数据集范围解释。
3. **评测隔离和 provenance 是硬门禁。** 错误标注、共享索引污染或旧报告覆盖新结果会产生虚假的质量提升。
4. **关键词检索不可被语义检索替代。** 代码、型号、法规编号和数字约束依赖 BM25 与消歧信号。
5. **Reranker 是可选收益，不是健康条件。** 小数据集或大 chunk 下可能只增加延迟，必须通过消融决定是否启用。
6. **长上下文必须基于 tokenizer 和消息结构裁剪。** 字符长度估算、拆散工具消息或无限摘要都会在长会话中失效。
7. **后台任务必须可观察、可恢复、幂等。** `create_task` 后静默失败不适合文档入库、记忆和恢复操作。
8. **数据库迁移和恢复必须先保护旧数据。** SQLite WAL、Windows 文件占用和多存储一致性需要专门处理。
9. **可选模型下载不能阻塞整个产品。** OCR/Reranker 应有独立状态、缓存、降级和手动恢复入口。
10. **公开文档只陈述当前事实。** 阶段评分、过期缺陷和实施计划应压缩为设计结论，而不是继续作为用户入口。

## 12. 已知限制与后续方向

不阻塞 `v0.1.0-beta`：

- 扩充复杂评测到至少 90 条，并固定 Dev/Test 分离；
- 增加中文长 PDF、复杂表格、OCR、无答案查询的独立容量与延迟基准；
- 为 `retry_wait` / `dead_letter` 增加管理界面和人工重放入口；
- 进一步减少 README 中可能过时的历史指标和阶段叙述；
- 建立第三方模型、数据集和依赖许可证清单。

未来若支持多实例或多租户，需要：

- 将主数据库迁移到 PostgreSQL；
- 使用独立 Worker 和任务队列；
- 对数据库、向量库、文件、缓存、备份和审计实现完整租户隔离；
- 引入完整 RBAC、OIDC/SSO、密钥轮换和生产部署基线；
- 重新定义备份、迁移和发布兼容策略。

---

本文是公开仓库唯一的 `docs/` 参考文档。原始阶段计划、专项评测过程和优化记录仅作为本地维护资料保存，不代表当前版本状态。
