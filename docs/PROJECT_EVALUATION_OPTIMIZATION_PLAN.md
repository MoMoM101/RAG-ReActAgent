# RAG Agent 项目复评与生产化优化设计

> 评测日期：2026-07-10  
> 评测对象：当前工作区（包含未提交修改）  
> 项目路径：`D:\Python\subject1\RAG_Agent`  
> 文档目标：将项目从“高完成度开源 Beta / 作品集项目”推进到“安全、可复现、可持续评测的生产候选版本”

---

## 1. 执行摘要

本项目已经形成完整的 RAG Agent 产品闭环：文档上传、解析、切片、向量化、混合检索、ReAct Agent、长期记忆、SSE 流式输出、设置管理、备份恢复和 React 前端均有实际实现，不是只停留在设计文档中的 Demo。

本次复评综合评分为 **7.1 / 10**。核心架构和业务测试质量较好，但存在三个阻止项目进入生产环境的关键缺口：

1. 备份恢复接口存在不安全解压、资源无限制和未认证访问风险。
2. CI 配置与当前代码状态不一致，干净环境无法直接通过全部质量门禁。
3. RAG 评测的 Recall 可能超过 100%，当前指标不能作为可靠的发布门槛。

本轮优化不以增加新功能为目标，优先修复安全边界、验证链条和评测可信度。全部 P0、P1 项完成后，目标评分为 **8.3 / 10**，达到可公开部署的 Beta 水平。

---

## 2. 评测范围与实测基线

### 2.1 代码规模

- 文件总数：约 256 个。
- Python 文件：124 个，约 11,600 行。
- TypeScript / TSX 文件：42 个，约 3,000 行。
- 测试及评测相关文件：81 个。
- 后端测试收集数：335 项。

### 2.2 实际验证结果

| 检查项 | 结果 | 说明 |
|---|---:|---|
| 后端测试（干净副本） | 22 passed / 8 skipped / 305 errors | `backend/data` 不存在，公共 fixture 无法打开 SQLite |
| 后端测试（手动创建 `backend/data` 后） | 323 passed / 12 skipped | 核心逻辑通过，存在 8 个 warning |
| Ruff | 失败，7 项 | 主要为 import 排序、无效变量和 `assert False` |
| Mypy | 失败，1 项 | `rag/query_classifier.py` 中 `ClassVar` 使用错误 |
| 前端构建 | 通过 | CSS 压缩阶段有 Tailwind at-rule warning |
| 前端测试 | 20 / 20 passed | 两个测试文件全部通过 |
| Oxlint | 通过，1 warning | `ConfirmDialog.tsx` Fast Refresh 导出警告 |

### 2.3 当前优势

- RAG 管线具备语义检索、BM25、RRF、自适应权重、去重、质量过滤和可选重排。
- Agent 具备意图分类、工具注册、错误重试、上下文缩窗、循环上限和最终总结。
- Calculator 使用 AST 白名单，没有直接执行模型生成的表达式。
- 长期记忆包含规则提取、LLM 确认、语义检索、去重和容量淘汰。
- 后台任务有统一管理、状态查询、异常日志和关闭清理。
- README、架构、配置、排障、设计和评测文档较完整。
- 前后端都已有自动化测试和 GitHub Actions 基础。

---

## 3. 优化目标与非目标

### 3.1 本轮目标

1. 任何用户上传的归档文件都不能写出允许的恢复目录，不能通过压缩炸弹耗尽资源。
2. 破坏性和敏感 API 必须具备明确的访问控制，默认部署不能无保护暴露到局域网。
3. 全新 clone 后，仅按 README 安装依赖即可跑通 lint、类型检查、测试和构建。
4. 检索评测所有指标满足数学定义，可重复运行并可用于 CI 回归门禁。
5. 增加答案级 RAG 评测，覆盖忠实度、引用和拒答，而不只评估召回列表。
6. 消除已知异步 warning，并为多工具调用提供受控并发能力。

### 3.2 非目标

- 本轮不引入 LangChain、LlamaIndex 等新 Agent 框架。
- 本轮不更换 Qdrant、SQLite、BM25 等核心存储选型。
- 本轮不实现完整多租户、RBAC 或企业级身份系统。
- 本轮不大规模重写前端视觉设计。

---

## 4. 优先级总览

| 优先级 | 模块 | 工作项 | 发布阻断 |
|---|---|---|:---:|
| P0 | 备份恢复 | 安全解压、资源限制、临时文件清理 | 是 |
| P0 | API 安全 | 敏感接口认证、安全绑定和部署默认值 | 是 |
| P0 | 恢复一致性 | 先校验、后替换；失败时保留原数据 | 是 |
| P1 | CI | 修复数据目录、Ruff、Mypy 和 warning | 是 |
| P1 | RAG 评测 | 修正 Recall/NDCG，改用稳定 qrels | 是 |
| P1 | 答案评测 | Faithfulness、引用、拒答、端到端成功率 | 建议 |
| P1 | Agent 性能 | 只读工具受控并发执行 | 否 |
| P1 | 配置生命周期 | 消除 import 阶段写 `.env` 等副作用 | 否 |
| P2 | 前端 | CSS warning、分包和错误状态完善 | 否 |
| P2 | 运维 | 结构化指标、压测、迁移和发布流程 | 否 |
| P2 | 文档 | 合并过期结论，建立状态索引 | 否 |

---

## 5. P0：备份恢复安全加固

### 5.1 当前问题

`backend/api/backup.py` 当前对上传内容执行 `tar.extractall(restore_dir)`，同时一次性 `await file.read()`。风险包括：

- `../` 绝对路径或链接成员导致目录穿越。
- 符号链接、硬链接、设备文件等非常规成员被解压。
- 上传文件、成员数和解压后总体积没有上限。
- 高压缩比归档导致内存、磁盘或 CPU 耗尽。
- 备份下载使用临时目录，但响应完成后没有明确删除临时文件。

### 5.2 设计方案

新增 `backend/utils/safe_archive.py`，集中实现安全归档逻辑，API 层不直接调用 `extractall()`。

建议接口：

```python
@dataclass(frozen=True)
class ArchiveLimits:
    upload_bytes: int = 512 * 1024 * 1024
    extracted_bytes: int = 2 * 1024 * 1024 * 1024
    member_count: int = 10_000
    single_member_bytes: int = 512 * 1024 * 1024


def validate_tar_members(
    tar: tarfile.TarFile,
    destination: Path,
    limits: ArchiveLimits,
) -> list[tarfile.TarInfo]:
    ...


def safe_extract_tar(
    tar: tarfile.TarFile,
    destination: Path,
    limits: ArchiveLimits,
) -> None:
    ...
```

校验规则：

1. 对每个成员计算 `destination / member.name` 的规范化绝对路径，确认其仍位于 `destination` 内。
2. 拒绝绝对路径、父目录跳转、符号链接、硬链接、设备文件和 FIFO。
3. 只允许普通文件和目录。
4. 校验成员数、单文件大小和所有文件声明大小之和。
5. 上传时分块读取并累计大小，超过限制立即终止，不把整个文件读入内存。
6. Python 3.12 环境同时使用 `filter="data"` 作为第二道防线，但不能用它替代显式路径和资源校验。

新增配置：

```env
BACKUP_MAX_UPLOAD_MB=512
BACKUP_MAX_EXTRACTED_MB=2048
BACKUP_MAX_MEMBERS=10000
```

备份下载通过 Starlette `BackgroundTask` 或专用生成器，在响应完成或断开后删除 `backup_dir`。

### 5.3 测试要求

新增 `backend/tests/api/test_backup_security.py`：

- 正常备份可以恢复。
- 拒绝 `../outside.txt`。
- 拒绝绝对路径。
- 拒绝 symlink、hardlink、device 和 FIFO。
- 拒绝超过成员数量上限的归档。
- 拒绝声明解压体积超过上限的归档。
- 拒绝上传体积超过限制的文件。
- 无论成功、失败或客户端断开，临时目录最终都会删除。

### 5.4 验收标准

- 恶意归档不能在恢复目录外创建或覆盖任何文件。
- 安全测试覆盖 Linux 和 Windows 路径形式。
- 对超限输入返回 413；格式或成员非法返回 400，不暴露本地绝对路径。
- Bandit 或等价静态扫描不再报告不安全 `tarfile.extractall`。

---

## 6. P0：API 认证与安全部署默认值

### 6.1 当前问题

项目没有实际的认证依赖，但以下接口能读取敏感数据或执行破坏性操作：

- 设置更新、连接测试、collection 重建和清空全部数据。
- 备份下载和恢复。
- 文档上传、删除和清空。
- 会话与记忆的读取和删除。

Docker 默认映射 `8000:8000`，本地启动器也可能绑定 `0.0.0.0`。CORS 只能限制浏览器跨域调用，不能阻止 curl、恶意本地程序或同源请求。

### 6.2 设计方案

本项目定位为单用户本地知识库，因此优先采用轻量管理令牌，而不是引入完整用户系统。

新增配置：

```env
ADMIN_API_TOKEN=
ALLOW_REMOTE_ACCESS=false
SERVER_HOST=127.0.0.1
```

新增 `backend/security.py`：

```python
admin_token = APIKeyHeader(name="X-Admin-Token", auto_error=False)


async def require_admin(token: str | None = Security(admin_token)) -> None:
    ...
```

规则：

1. 首次启动生成高熵 `ADMIN_API_TOKEN`，只在本地控制台显示一次，持久化时使用受限文件权限。
2. 使用 `secrets.compare_digest()` 比较令牌。
3. 除 `/api/health` 和静态资源外，默认所有业务 API 都要求令牌；如果需要降低迁移成本，至少先保护写接口、设置、备份和记忆。
4. 前端只在当前浏览器会话保存令牌，不写入日志、错误消息和 URL。
5. 默认绑定 `127.0.0.1`。仅当 `ALLOW_REMOTE_ACCESS=true` 且显式配置令牌时允许绑定 `0.0.0.0`。
6. Docker Compose 默认仅映射到 `127.0.0.1:8000:8000` 和 `127.0.0.1:5173:5173`。
7. Qdrant 的 6333/6334 端口不应默认暴露给宿主机；只有调试 profile 才开放。

### 6.3 验收标准

- 未提供令牌访问敏感接口返回 401。
- 错误令牌返回 401，正确令牌可以访问。
- `/api/health` 在无令牌时仍可用于容器健康检查。
- 默认 Docker 启动后，局域网其他机器不能直接访问后端和 Qdrant。
- 日志、SSE、异常响应和设置读取接口不泄漏完整令牌或 API Key。

---

## 7. P0：恢复流程的一致性与可回滚性

### 7.1 当前问题

现有恢复流程会提前 dispose 数据库并覆盖正式 SQLite 文件，然后删除向量与 BM25 数据并重新入库。如果中途失败，可能出现：

- SQLite 已替换但向量索引仍是旧版本。
- 文档记录已删除，但部分文件重新入库失败。
- 上传目录已被整体删除，恢复失败后原文件无法自动回退。
- 恢复进行时仍有并发请求读写数据。

### 7.2 设计方案

采用“校验 → 暂存 → 构建 → 原子切换 → 延迟清理”流程：

```text
上传归档
  → 安全解压到 restore staging
  → SQLite integrity_check + schema/version 校验
  → 校验上传文件与 Document 记录对应关系
  → 在独立临时 collection / BM25 表重建索引
  → 获取全局维护锁，暂停写请求
  → 原子替换 SQLite、uploads 和 active collection 指针
  → 健康检查
  → 成功后异步删除旧资源；失败则切回旧指针
```

实现要点：

- 新增全局 maintenance 状态；恢复时写接口返回 503 和 `Retry-After`。
- SQLite 先执行 `PRAGMA integrity_check`。
- 备份中保存 `manifest.json`，包含格式版本、创建时间、文件 SHA-256、应用版本和 collection 配置。
- 恢复前验证 manifest 及每个文件的哈希。
- 采用现有 collection 双缓冲思路，不在正式索引上原地删除重建。
- 保留旧数据库、上传目录和 collection，直到新状态健康检查通过。

### 7.3 验收标准

- 在恢复的每个阶段注入异常，系统都能继续使用恢复前的数据。
- 恢复期间不会发生部分新数据、部分旧索引的混合读取。
- 恢复任务可查询进度、失败原因和回滚结果。
- 同一时间只允许一个备份恢复或 collection 重建任务。

---

## 8. P1：CI 和干净环境可复现性

### 8.1 数据目录初始化

`tests/conftest.py` 设置 `./data/test_rag_agent.db`，但没有创建父目录。应在任何应用模块导入之前创建测试根目录，最好使用 pytest 临时目录而不是仓库目录。

推荐方案：

```python
def pytest_configure(config):
    root = Path(tempfile.mkdtemp(prefix="rag_agent_tests_"))
    os.environ["DATABASE_URL"] = f"sqlite+aiosqlite:///{root / 'test.db'}"
    os.environ["QDRANT_PATH"] = str(root / "qdrant")
    os.environ["UPLOAD_DIR"] = str(root / "uploads")
```

更稳妥的长期方案是让 `Settings` 和数据库 engine 通过 fixture 注入，避免测试依赖模块导入顺序。

### 8.2 修复质量门禁

必须修复：

- Ruff 的 7 项问题。
- `rag/query_classifier.py` 的 Mypy `ClassVar` 错误。
- Web Search 测试产生的未 await 协程 warning。
- 前端 `ConfirmDialog.tsx` 非组件导出 warning。
- Tailwind / Lightning CSS 对 `@theme`、`@tailwind` 的构建 warning。

将 warning 逐步升级为门禁：

```ini
[pytest]
filterwarnings =
    error::RuntimeWarning
```

第三方库无法立即处理的 DeprecationWarning 应按模块精确忽略，禁止全局忽略所有 warning。

### 8.3 CI 结构调整

建议拆成以下 job：

1. `backend-static`：Ruff + Mypy。
2. `backend-unit`：不需要 Qdrant、模型和网络的快速测试。
3. `backend-integration`：SQLite/Qdrant 本地模式、加载器和 API 测试。
4. `frontend-quality`：npm ci + lint + test + build。
5. `security`：依赖漏洞扫描、Bandit、安全归档测试。
6. `docker-smoke`：构建镜像，启动后检查 health 与无 Key 降级状态。

### 8.4 验收命令

```bash
cd backend
ruff check . --config ../pyproject.toml
python -m mypy . --config-file ../pyproject.toml
pytest tests/ -W error::RuntimeWarning --cov=. --cov-fail-under=70

cd ../frontend
npm ci
npm run lint
npm test
npm run build

docker compose build
docker compose up -d
curl --fail http://127.0.0.1:8000/api/health
```

所有命令必须在没有 `.env`、`data/`、缓存目录和本地模型的全新 clone 中执行成功。

---

## 9. P1：重构检索评测指标

### 9.1 当前问题

当前 `compute_metrics()` 先用 Jaccard 判断每个返回文本是否匹配任意 ground truth，再用相关返回结果数除以 ground truth 数计算 Recall。多个返回 chunk 可以匹配同一个 ground truth，导致重复计数，已保存结果中 Recall 出现 200%～300%。

此外：

- NDCG 的 ideal relevance 来源于本次已检索结果，可能高估排序质量。
- 文本相似度判断会受 chunk_size、重叠和分词影响，ground truth 不稳定。
- 评测集规模较小，且开发、调参和最终报告使用同一批数据。
- rerank 前后排序结果完全一致时仍输出 rerank 指标，没有明确说明模型是否真实加载。

### 9.2 qrels 数据模型

用稳定标识替代文本 Jaccard 作为主判定依据：

```json
{
  "query_id": "business-001",
  "query": "ERR_40003",
  "relevant": [
    {"document_key": "paygate", "section_key": "error-40003", "grade": 3}
  ],
  "expected_answer_facts": ["amount exceeds one million"],
  "must_cite": ["paygate#error-40003"]
}
```

入库时把稳定的 `document_key`、标题路径和 `section_key` 写入 chunk metadata。即使 chunk_size 改变，只要命中对应 section，都视为命中同一 relevant item。

### 9.3 指标定义

- `Precision@k = top-k 中相关结果数 / k`。
- `Recall@k = top-k 命中的唯一 relevant item 数 / relevant item 总数`。
- `MRR` 使用第一个相关结果排名的倒数。
- `NDCG@k` 使用完整 qrels 的等级相关性构造 IDCG。
- 同一 relevant item 被多个重叠 chunk 命中时只计一次 Recall。
- 额外报告去重前后的 Precision，监控重叠 chunk 污染。
- 所有比例指标必须断言位于 `[0, 1]`。

### 9.4 数据集拆分

- `dev`：用于 chunk、RRF 权重和阈值调参。
- `test`：发布评测，只在版本冻结后运行。
- `challenge`：跨文档、多跳、否定问题、近似术语、版本冲突和不可回答问题。

第一阶段至少扩充到 100 条查询，覆盖：

- 精确错误码、SKU、条款和数字。
- 中文自然语言与中英混合查询。
- 同义改写和指代追问。
- 跨文档比较与多跳问题。
- 文档中不存在答案的问题。
- 恶意 prompt injection 文档片段。

### 9.5 验收标准

- Recall、Precision、MRR、NDCG、Hit Rate 永不超过 1。
- 对重复 chunk、同 section 多 chunk 和跨文档多答案有单元测试。
- reranker 未加载时结果明确标记 `reranker_status=unavailable`，不得伪装成启用评测。
- 每次评测保存代码 commit、配置、模型、数据集版本、随机种子和耗时分位数。

---

## 10. P1：增加答案级 RAG 评测

检索命中不等于最终回答正确。应新增端到端评测，至少包含：

| 指标 | 定义 |
|---|---|
| Answer correctness | 预期关键事实的覆盖率 |
| Faithfulness | 回答中的事实是否能由检索上下文支持 |
| Citation precision | 引用来源是否真的支持对应陈述 |
| Citation recall | 需要引用的陈述是否都有来源 |
| Abstention accuracy | 无答案时是否正确拒答 |
| Tool selection accuracy | Agent 是否选择正确工具 |
| Task success rate | 从问题到最终答案的整体成功率 |
| Latency / cost | P50、P95 延迟，LLM 和 embedding token 成本 |

建议保留两类判定：

1. 确定性判定：关键字、数值容差、引用 section、工具调用轨迹。
2. LLM-as-judge：只用于难以规则化的语义完整性，并固定模型、温度和 rubric；抽样进行人工复核。

发布初始门槛建议：

- Hit@5 ≥ 95%。
- MRR ≥ 90%。
- Faithfulness ≥ 95%。
- Citation precision ≥ 95%。
- 不可回答问题正确拒答率 ≥ 90%。
- P95 检索延迟 ≤ 1 秒，不含远程 LLM 生成。

门槛需要在修正后的独立 test 集上重新建立，不能沿用当前 JSON 中的数值。

---

## 11. P1：Agent 多工具受控并发

### 11.1 当前问题

Agent 可以接收一次 LLM 返回的多个 tool call，但当前使用 `for` 循环逐个 `await registry.execute()`，实际为串行执行。多个网络或检索工具组合时，延迟线性叠加。

### 11.2 设计方案

在工具元数据中增加：

```python
class BaseTool:
    parallel_safe: bool = True
    side_effecting: bool = False
```

执行策略：

1. 连续只读且 `parallel_safe` 的工具使用 `asyncio.gather()` 并发执行。
2. 写操作、有依赖关系或同资源互斥工具保持串行。
3. 使用全局 semaphore 控制并发数，默认 4。
4. 单个工具失败不取消其他独立工具。
5. 最终 tool message 顺序仍按原始 tool call 顺序写回，保证模型协议稳定。
6. 每个工具独立记录耗时、重试次数和错误类别。

### 11.3 验收标准

- 两个各延迟 200ms 的只读工具总体耗时显著低于 400ms。
- 一个工具失败时另一个工具结果仍返回给 LLM。
- 副作用工具不会并发执行。
- 超时和请求取消能向所有未完成子任务传播。

---

## 12. P1：配置与启动生命周期清理

### 12.1 当前问题

`config.py` 在模块导入期间可能生成密钥、写 `.env`、解密 API Key 和恢复 collection 指针。import 副作用会造成：

- 测试依赖导入顺序。
- 只读文件系统或容器 Secret 场景启动失败。
- 多 worker 同时写 `.env` 的竞态。
- 静态检查或脚本仅导入模块也改变磁盘状态。

### 12.2 设计方案

- `Settings()` 只负责读取和验证配置，不执行写操作。
- 密钥初始化移动到显式 `bootstrap_secrets()`。
- collection 指针恢复移动到 FastAPI lifespan。
- API Key 解密放到 Secret Provider 层，返回 `SecretStr`。
- 生产环境优先从环境变量、Docker Secret 或 Secret Manager 读取，不回写 `.env`。
- 开发模式需要自动生成时，使用文件锁、原子临时文件替换和受限权限。

### 12.3 验收标准

- `python -c "import config"` 不产生或修改任何文件。
- 只读容器文件系统可正常启动。
- 两个并发 worker 不会生成不同密钥或破坏 `.env`。
- 设置和日志中的 Secret 始终以掩码显示。

---

## 13. P2：前端和用户体验

### 13.1 构建清洁度

- 确认 Tailwind 4 与 Vite/Lightning CSS 的标准集成方式，消除未知 `@theme`、`@tailwind` warning。
- 把 `ConfirmDialog.tsx` 的共享常量或 helper 移到独立文件，消除 Fast Refresh warning。
- CI 对新增 warning 失败，已知第三方 warning 使用精确白名单。

### 13.2 性能

当前主 JS 约 442 KB、gzip 后约 137 KB，仍可接受，但可以按路由拆分：

- Settings、Memories 和 Document Chunk Viewer 使用懒加载。
- Markdown 渲染器与非首屏模块独立 chunk。
- 设置 bundle size budget，例如主入口 gzip ≤ 120 KB。

### 13.3 错误和安全交互

- 统一显示 401、413、429、503 和恢复维护状态。
- SSE 断开后展示可重试状态，不重复写入用户消息。
- 管理令牌只保存在 session 范围，提供锁定和重新输入入口。
- 备份恢复显示校验、暂存、重建、切换、完成等阶段进度。

---

## 14. P2：运维、可观测性与数据迁移

### 14.1 指标

增加 Prometheus 或结构化指标端点：

- HTTP 请求量、错误率和 P50/P95/P99。
- Agent 循环次数、工具成功率、重试率和超时率。
- 检索各阶段耗时：embedding、vector、BM25、fusion、rerank。
- 入库队列长度、各状态耗时和失败原因。
- LLM/embedding token 用量与估算成本。
- Qdrant、SQLite、磁盘和后台任务健康状态。

指标不得包含原始用户问题、文档正文、API Key 或管理令牌。

### 14.2 数据库迁移

当前项目应从“启动时 create_all”逐步迁移到 Alembic：

- 每个版本有明确 schema revision。
- 启动时只检查迁移状态，不隐式做破坏性迁移。
- 备份 manifest 记录 schema revision。
- 恢复旧备份时先在 staging 数据库运行迁移。

### 14.3 压测场景

至少覆盖：

- 20 个并发 SSE 聊天。
- 3 个并发大文档入库，同时执行检索。
- 10 万、100 万 chunk 下的搜索延迟和内存占用。
- LLM 超时、Qdrant 暂时不可用、SQLite busy 和磁盘空间不足。
- 客户端中途取消上传、聊天和恢复任务。

---

## 15. 文档治理

项目已有较多按日期积累的设计和评测文档，部分结论已被后续实现取代。建议新增 `docs/STATUS.md` 作为唯一状态入口：

```text
当前版本能力
当前已知问题
当前评测基线
正在实施的计划
已完成计划及对应 commit
过期文档索引
```

规则：

- 设计文档保留历史，不直接删除。
- 完成后在文档顶部标注 `status: completed` 和对应 commit。
- 已被取代的文档标注 `superseded_by`。
- README 只展示经过当前 CI 和评测脚本验证的指标。
- 自动测试 `.env.example`、README 默认值与 `Settings` 一致。

---

## 16. 实施阶段与里程碑

### Phase 1：安全发布阻断项

范围：安全解压、资源限制、API 认证、安全网络绑定、恢复回滚。

完成定义：

- 所有 P0 安全测试通过。
- 默认部署不向局域网暴露未认证管理接口。
- 恢复任意阶段失败均不破坏现有可用数据。

### Phase 2：工程验证闭环

范围：测试临时目录、Ruff、Mypy、异步 warning、前端 warning、CI 拆分。

完成定义：

- 全新 clone 的前后端 CI 全绿。
- 后端覆盖率 ≥ 70%。
- 项目自身 RuntimeWarning 为 0。

### Phase 3：可信评测

范围：qrels、正确 IR 指标、数据集拆分、答案级评测和发布门槛。

完成定义：

- 比例指标全部满足 `[0,1]`。
- 至少 100 条独立查询和不可回答样本。
- README 指标由固定命令可重复生成。

### Phase 4：性能和可运维性

范围：工具并发、指标、压测、迁移、前端分包和文档状态页。

完成定义：

- 有明确并发容量与 P95 延迟基线。
- 关键失败场景有降级、告警和恢复路径。
- 发布、升级、备份和回滚流程均有演练记录。

---

## 17. 建议任务拆分

| ID | 任务 | 依赖 | 估算 |
|---|---|---|---:|
| SEC-01 | 安全 tar 校验与解压工具 | 无 | 1 天 |
| SEC-02 | 备份上传/解压资源限制 | SEC-01 | 0.5 天 |
| SEC-03 | 管理令牌与 API 保护 | 无 | 1 天 |
| SEC-04 | Docker 和启动器安全绑定 | SEC-03 | 0.5 天 |
| BAK-01 | manifest、完整性校验和 staged restore | SEC-01 | 1.5 天 |
| BAK-02 | 恢复维护锁、原子切换和回滚测试 | BAK-01 | 1.5 天 |
| CI-01 | pytest 临时目录与 fixture 隔离 | 无 | 0.5 天 |
| CI-02 | Ruff/Mypy/RuntimeWarning 清零 | CI-01 | 0.5 天 |
| CI-03 | CI job 拆分和 Docker smoke | CI-02 | 0.5 天 |
| EVAL-01 | qrels schema 和稳定 section metadata | 无 | 1 天 |
| EVAL-02 | IR 指标重写与单元测试 | EVAL-01 | 1 天 |
| EVAL-03 | 答案级评测和 challenge 集 | EVAL-02 | 2 天 |
| AGENT-01 | 工具并发元数据和执行器 | CI-02 | 1 天 |
| OPS-01 | 指标、压测和容量报告 | AGENT-01 | 1.5 天 |

估算仅用于排序。安全和恢复工作应优先保证正确性，不应为追求工期并行修改同一数据切换链路。

---

## 18. 最终验收清单

### 安全

- [ ] 归档路径穿越、链接、设备文件和压缩炸弹测试全部通过。
- [ ] 敏感与破坏性 API 默认需要认证。
- [ ] 默认仅监听本机，Qdrant 不向宿主机公开。
- [ ] Secret 不出现在响应、日志、URL 和前端持久存储中。

### 可靠性

- [ ] 恢复失败可完整回滚。
- [ ] collection、BM25、SQLite 和 uploads 始终处于同一版本。
- [ ] 后台任务、恢复、重建都有状态和失败原因。

### 工程质量

- [ ] Ruff、Mypy、pytest、前端 lint/test/build 全部通过。
- [ ] 全新 clone 不需要预创建 `data/` 或其他未跟踪目录。
- [ ] RuntimeWarning 为 0，第三方 warning 精确管理。
- [ ] 后端覆盖率不低于 70%。

### 评测

- [ ] IR 指标数学定义正确且都在 `[0,1]`。
- [ ] 开发集、测试集和 challenge 集分离。
- [ ] 有答案忠实度、引用和拒答指标。
- [ ] README 中的性能数据可由固定版本和命令重现。

### 运维

- [ ] 有 P50/P95 延迟、错误率、工具重试和入库队列指标。
- [ ] 完成并发聊天、并发入库和依赖故障压测。
- [ ] 升级、备份、恢复和回滚流程都有文档和演练。

---

## 19. 预期结果

完成 P0 后，项目可以消除最直接的远程破坏和归档输入风险；完成 P1 后，项目具备可信的 CI 与 RAG 质量基线；完成 P2 后，项目将具备持续发布、容量评估和故障定位能力。

届时项目定位可从：

> 高完成度个人 RAG Agent / 开源 Beta

提升为：

> 默认安全、评测可信、可回滚、可观测的单用户生产候选 RAG Agent

