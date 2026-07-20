# RAG Agent 安全加固优化收尾流程

> 日期：2026-07-11  
> 当前优化位置：`.claude/worktrees/phase1-security-hardening`  
> 当前根工作区：`D:\Python\subject1\RAG_Agent`  
> 当前复评分：根工作区 7.3 / 10，安全加固 worktree 7.9 / 10  
> 目标：安全加固代码可验证、可合并、可回滚，发布候选评分达到 8.4～8.6 / 10

---

## 1. 流程目标

当前安全加固 worktree 已经完成大量有效改造：

- OCR/Rerank 延迟导入与组件状态机。
- 最小安装无可选模型时可以启动。
- 安全 tar 解压、大小限制、manifest 和哈希校验。
- 管理令牌、维护锁和安全网络默认值。
- qrels v2、正确的 Recall/NDCG 和答案级评测结构。
- 工具并发、结构化指标和 Docker smoke 基础。

但还存在两个 P0 和若干发布门禁：

1. staged restore 实际仍修改正式数据库和索引，失败回滚不能恢复 Qdrant/BM25。
2. 后端自动启用管理令牌，但前端没有发送令牌，核心页面返回 401。
3. Docker Compose 的容器监听地址和 Vite proxy 地址错误。
4. 后端测试仍有 3 个失败、9 个 warning，Ruff 仍有 10 项。
5. 新 qrels v2 尚未替换正式评测入口和 README 指标。
6. 所有改动仍停留在未提交 worktree，根工作区还有重叠的未提交修改。

本流程的核心原则是：

> 先固定和保护现状，再修复 P0；每个阶段独立提交、独立验证；只有所有门禁全绿后才合并回主工作区。

---

## 2. 总体流程

```text
0. 冻结现场与建立安全基线
   ↓ Gate 0：两个工作区都可恢复
1. 清理静态门禁
   ↓ Gate 1：Ruff + Mypy 全绿
2. 完成认证端到端闭环
   ↓ Gate 2：前端、SSE、E2E 全部带认证工作
3. 重构 staged restore
   ↓ Gate 3：SQLite/Qdrant/BM25/uploads 一致切换和回滚
4. 修复 Docker Compose 与部署验证
   ↓ Gate 4：真实 compose smoke 通过
5. 迁移到 qrels v2 正式评测
   ↓ Gate 5：指标可信并重新生成基线
6. 清零测试失败与项目 warning
   ↓ Gate 6：完整 CI 全绿
7. 整理提交并合并 worktree
   ↓ Gate 7：根工作区合并后复测全绿
8. 发布候选验收
```

任何 Gate 失败都不得继续进入下一阶段。禁止把多个未验证阶段堆积在一个超大提交中。

---

## 3. Phase 0：冻结现场与建立安全基线

### 3.1 当前 Git 状态风险

项目存在两个不同状态：

- 根工作区包含约 9 个已修改文件和多份未跟踪文档。
- `phase1-security-hardening` worktree 包含约 29 个修改文件和多个新增模块，尚未提交。

两边修改了 `backend/main.py`、`backend/api/chat.py`、测试配置等重叠文件。直接合并或复制文件容易丢失一侧改动。

### 3.2 冻结规则

在 worktree 中继续优化期间：

- 不直接修改根工作区业务代码。
- 不使用 `git reset --hard`、`git checkout -- .` 等丢弃命令。
- 不从根目录整目录覆盖 worktree。
- 每个阶段先查看 `git status` 和 `git diff --check`。
- 所有新增文件先纳入 Git，再做重构。

### 3.3 建议检查点

在根工作区：

```bash
git status --short
git diff --check
git diff > ../rag-agent-root-before-hardening.patch
```

在安全加固 worktree：

```bash
git status --short
git diff --check
git diff > ../rag-agent-phase1-before-completion.patch
```

建议为根工作区当前改动建立独立保护分支并提交，避免后续合并依赖 stash。提交前应确认文档和代码分别属于哪个变更主题。

### 3.4 Gate 0

- [ ] 根工作区 diff 已导出或提交到保护分支。
- [ ] worktree diff 已导出。
- [ ] 两个工作区不存在无法解释的二进制或 Secret 文件。
- [ ] `.env`、模型权重、数据库和上传文件未加入 Git。
- [ ] 已记录当前 commit `2817a3b` 及 worktree 分支名。

---

## 4. Phase 1：清理静态质量门禁

### 4.1 Ruff

当前 10 项均为 import 排序，可在安全加固 worktree 中执行：

```bash
ruff check backend --config pyproject.toml --fix
ruff check backend --config pyproject.toml
```

重点文件：

- `backend/api/backup.py`
- `backend/api/chat.py`
- `backend/api/documents.py`
- `backend/main.py`
- `backend/rag/pipeline.py`
- `backend/tests/worker/test_tasks.py`

自动修复后必须人工检查函数内延迟 import 是否仍保持正确顺序，避免把有意延迟加载的可选依赖移动到模块顶部。

### 4.2 Mypy

当前实测已经通过 132 个源文件。将该结果保留为门禁：

```bash
python -m mypy backend --config-file pyproject.toml
```

不允许通过扩大 `ignore_missing_imports` 或新增全局 ignore 来隐藏新增错误。

### 4.3 第一阶段提交

建议提交信息：

```text
style: make security hardening pass static quality gates
```

该提交只允许包含格式和静态类型修复，不混入功能行为改变。

### 4.4 Gate 1

- [ ] Ruff 0 error。
- [ ] Mypy 0 error。
- [ ] `git diff --check` 无空白错误。
- [ ] 最小安装启动测试仍通过。

---

## 5. Phase 2：完成认证端到端闭环

### 5.1 当前问题

后端首次启动会自动生成 `ADMIN_API_TOKEN`，并保护文档、会话、设置、记忆和备份路由。前端 API 客户端没有发送 `X-Admin-Token`，导致：

- 文档列表 401。
- 创建和删除会话 401。
- 设置、记忆、备份等页面不可用。
- 后端 E2E 有 3 个失败。
- Chat 路由未保护，与其他路由认证边界不一致。

### 5.2 先确定认证产品策略

采用本地单用户令牌模式：

- `/api/health` 保持公开。
- `/api/health/dependencies` 可公开，但不返回错误堆栈或 Secret。
- `/api/metrics` 默认要求管理令牌，避免暴露内部运行数据。
- 所有业务 API，包括 Chat，都要求管理令牌。
- 令牌由用户显式输入，前端只保存到 `sessionStorage`。
- 不将管理令牌编译进前端 bundle。
- 不将令牌放入 URL query；如果使用 URL fragment 做首次引导，读取后立即清除地址栏 fragment。

### 5.3 前端认证状态

新增：

```text
frontend/src/stores/authStore.ts
frontend/src/components/auth/TokenGate.tsx
```

建议接口：

```ts
type AuthState = {
  token: string;
  authenticated: boolean;
  setToken: (token: string) => void;
  clearToken: () => void;
};
```

规则：

- Token 只保存到 `sessionStorage`。
- 页面刷新保留，关闭浏览器会话后清除。
- 不写入 localStorage、日志、埋点和错误信息。
- 首次进入时用健康接口判断后端可达，再显示令牌输入页。

### 5.4 API 客户端统一注入

修改 `frontend/src/api/client.ts`：

```ts
function authHeaders(extra: HeadersInit = {}): HeadersInit {
  const token = sessionStorage.getItem("rag_admin_token");
  return token
    ? { ...extra, "X-Admin-Token": token }
    : extra;
}
```

所有 GET、POST、PUT、DELETE、FormData 上传和流式请求必须复用同一 header 构造器。

统一处理 401：

1. 清除 session token。
2. 发出 `auth:required` 状态。
3. 跳转 TokenGate。
4. 不自动无限重试原请求。

### 5.5 SSE/流式聊天认证

原生 `EventSource` 无法设置自定义 Header。如果当前聊天使用 fetch 读取 SSE 流，应直接加 Header；如果使用 EventSource，应改为：

- `fetch()` POST/GET + ReadableStream 解析 SSE；或
- 仅为浏览器建立短期一次性 session cookie。

不推荐把长期管理令牌放在 SSE URL query 中。

### 5.6 后端边界统一

将 Chat router 也加入：

```python
app.include_router(chat_router, dependencies=[Depends(require_admin)])
```

`/api/metrics` 加认证；健康端点和静态前端保持公开。

### 5.7 启动令牌体验

当前日志只说明令牌已保存，用户不知道如何取得。建议：

- 首次生成时打印 `.env` 文件位置和操作提示，不打印完整令牌到长期日志。
- 统一启动器可以安全读取令牌，并只在交互式终端显示一次。
- Docker 用户通过环境变量显式传入令牌。
- 如果容器中未提供 token，则启动失败或打印明确的高优先级错误，不在不可持久化层静默生成。

### 5.8 测试

新增或修改：

- 无令牌访问业务 API 返回 401。
- 错误令牌返回 401。
- 正确令牌返回原本业务状态码。
- Chat、上传、FormData、SSE 都携带令牌。
- 前端 401 清除 token 并回到 TokenGate。
- Token 不出现在 URL、日志和错误对象。
- 原 3 个 E2E 测试使用测试令牌后恢复通过。

### 5.9 第二阶段提交

建议提交信息：

```text
feat: complete admin-token authentication across frontend and backend
```

### 5.10 Gate 2

- [ ] 前端所有 API 客户端统一携带令牌。
- [ ] Chat/SSE 完成认证。
- [ ] 所有受保护路由认证边界一致。
- [ ] 认证相关前后端测试全部通过。
- [ ] 原 3 个 E2E 失败全部消失。

---

## 6. Phase 3：重构 staged restore，保证四层数据一致

### 6.1 当前 P0 根因

当前恢复流程虽然创建了 `staging_dir`，但构建阶段仍使用全局：

- `async_session`
- 正式 Qdrant collection
- 正式 BM25 表
- `ingest_document()` 自动生成的新 document UUID

随后又用备份 SQLite 覆盖正式数据库。因此可能出现：

```text
SQLite.document.id       = 备份中的旧 UUID
Qdrant.document_id       = 重新入库生成的新 UUID
BM25.document_id         = 重新入库生成的新 UUID
```

当前 rollback 只恢复 SQLite 和 uploads，没有恢复已被删除或重建的 Qdrant/BM25。

### 6.2 恢复一致性模型

恢复必须把以下资源视为同一个版本单元：

```text
SQLite metadata + BM25 tables
Qdrant active collection
uploads directory
active collection pointer / manifest
```

任何一步失败，都必须继续使用恢复前的完整版本。

### 6.3 禁止事项

在原子切换前禁止：

- 删除正式 Document 记录。
- 删除正式 Qdrant points。
- 删除或改名正式 BM25 表。
- 覆盖正式 uploads。
- 调用默认 `ingest_document()` 向正式资源写入。

### 6.4 推荐实现架构

引入显式恢复上下文：

```python
@dataclass
class RestoreTarget:
    database_url: str
    upload_dir: Path
    qdrant_collection: str
    bm25_table_suffix: str
    preserve_document_ids: bool = True
```

索引构建函数不能依赖全局 settings：

```python
async def build_document_indexes(
    document: RestoreDocument,
    target: RestoreTarget,
    embedding: BaseEmbedding,
) -> BuildResult:
    ...
```

`RestoreDocument` 必须带备份中的原始 ID：

```python
@dataclass
class RestoreDocument:
    id: str
    filename: str
    file_type: str
    file_hash: str
```

chunk ID 继续使用：

```python
uuid5(NAMESPACE_DNS, f"{original_document_id}:{chunk_index}")
```

### 6.5 推荐恢复流程

```text
1. 获取 maintenance lock
2. 分块读取并安全解压
3. 校验 manifest、SHA-256、格式版本和 SQLite integrity_check
4. 打开独立 staging SQLite engine
5. 校验备份 Document 与 uploads 文件对应关系
6. 创建临时 Qdrant collection: rag_chunks_restore_<uuid>
7. 使用原始 document ID 构建临时向量索引
8. 在 staging SQLite 内校验或重建 BM25 表
9. 对 staging 执行交叉一致性检查
10. 暂停写请求
11. 原子切换 SQLite/uploads/Qdrant pointer
12. 重新创建正式 engine/session
13. 执行健康与抽样检索检查
14. 成功后延迟删除旧 collection 和旧文件
15. 失败则切回全部旧指针与旧资源
```

### 6.6 BM25 策略

BM25 当前存放在 SQLite 表中，优先采用：

- 如果备份数据库包含完整且版本兼容的 BM25 表，直接校验并随 SQLite 一起切换。
- 如果必须重建，使用指向 staging SQLite 的独立 engine，而不是全局 engine。
- 切换前比较 Document 数、chunk 数和索引表记录数。

### 6.7 Qdrant 双缓冲

临时 collection 规则：

```text
rag_chunks_restore_<short_uuid>
```

完整构建后写入临时 pointer 文件，再通过原子替换更新 active pointer。旧 collection 至少保留一个回滚窗口，不立即删除。

### 6.8 原子文件切换

`shutil.copy2()` 不是跨资源事务。单文件采用：

```text
write/copy to sibling temporary file
fsync
os.replace(temp, live)
```

uploads 目录使用版本化目录与指针：

```text
uploads_versions/<restore_id>/
active_uploads.json
```

避免先 `rmtree(live)` 再 copy，防止进程崩溃后目录为空。

### 6.9 交叉一致性检查

切换前后都必须检查：

- ready Document 数量。
- 每个 Document 的 chunk_count。
- Qdrant 中每个 document_id 的 point 数。
- BM25 中每个 document_id 的 chunk 数。
- 所有 ready 文档对应的上传文件存在且哈希正确。
- Qdrant/BM25 中不存在数据库之外的 document_id。

### 6.10 故障注入测试

至少在以下位置抛异常：

- 安全解压后。
- manifest 校验后。
- staging SQLite 打开后。
- 临时 Qdrant 构建到 50%。
- BM25 构建后。
- SQLite 切换后、Qdrant pointer 切换前。
- pointer 切换后、健康检查前。
- 健康检查失败。

每个测试都断言：

- 原数据库仍可查询。
- 原 Qdrant/BM25 仍能命中。
- 原 uploads 未丢失。
- maintenance lock 最终释放。
- 临时资源最终清理或进入可清理状态。

### 6.11 第三阶段提交

建议拆成两个提交：

```text
refactor: decouple ingestion targets for staged index builds
feat: make backup restore atomic across database and indexes
```

### 6.12 Gate 3

- [ ] 切换前不修改任何正式数据或索引。
- [ ] 恢复保持原始 document ID。
- [ ] SQLite、Qdrant、BM25、uploads 同版本切换。
- [ ] 所有故障注入场景都能完整回滚。
- [ ] 恢复前后抽样检索结果一致。

---

## 7. Phase 4：修复 Docker Compose 网络与真实 smoke

### 7.1 后端容器监听

容器内部必须监听所有接口：

```yaml
environment:
  SERVER_HOST: 0.0.0.0
ports:
  - "127.0.0.1:8000:8000"
```

安全性由宿主机端口只绑定 `127.0.0.1` 保证，而不是让容器进程监听容器 loopback。

### 7.2 前端 proxy

当前 Vite proxy 指向 `http://localhost:8000`。在前端容器内它指向前端容器自身。

修改：

```ts
const proxyTarget = process.env.VITE_API_PROXY_TARGET ?? "http://localhost:8000";

export default defineConfig({
  server: {
    proxy: {
      "/api": {
        target: proxyTarget,
        changeOrigin: true,
      },
    },
  },
});
```

Compose：

```yaml
frontend:
  environment:
    VITE_API_PROXY_TARGET: http://backend:8000
```

### 7.3 Compose smoke

现有 CI 只运行独立 backend 镜像，无法发现 Compose 服务间网络问题。新增：

```bash
docker compose up -d --build
docker compose ps
curl --fail http://127.0.0.1:8000/api/health
curl --fail http://127.0.0.1:5173/
curl --fail -H "X-Admin-Token: test-token" \
  http://127.0.0.1:5173/api/documents
docker compose down -v
```

测试请求必须通过前端 proxy 访问后端，才能验证完整链路。

### 7.4 第四阶段提交

```text
fix: make compose networking and frontend proxy work end to end
```

### 7.5 Gate 4

- [ ] 后端容器从宿主机 localhost 可访问。
- [ ] 前端容器通过 service name 访问 backend。
- [ ] 通过 5173 `/api` proxy 可完成带认证请求。
- [ ] Qdrant 端口默认不向宿主机暴露。
- [ ] Compose smoke 在 CI 中通过。

---

## 8. Phase 5：迁移正式评测到 qrels v2

### 8.1 当前双轨问题

项目已经新增正确的 `eval_metrics.py`、qrels schema 和答案级评测，但旧 `evaluate_rag.py` 仍使用可能超过 100% 的 Recall 算法。README 和旧 JSON 也仍引用旧指标。

### 8.2 迁移策略

1. 将旧 `compute_metrics()` 标记为 legacy，不再作为正式报告入口。
2. 正式 runner 统一使用 `compute_metrics_v2()`。
3. 检索结果必须携带稳定 `document_key` 和 `section_key`。
4. 找不到稳定 section 时明确记为 unmatched，不回退到宽松 Jaccard 并伪装正式 qrel。
5. 重新构建空评测库，禁止使用开发数据库残留数据。
6. 重新运行无 Rerank和有 Rerank两组实验。
7. Rerank 只有在 `rerank_applied=true` 时才计入启用组。

### 8.3 数据集门槛

当前 qrels v2 是正确起点，但发布报告至少需要：

- 100 条以上查询。
- dev/test/challenge 隔离。
- 精确码、自然语言、跨文档、多跳、无答案和冲突版本。
- 至少一部分人工复核标注。

### 8.4 正式输出

结果文件应包含：

```json
{
  "commit": "...",
  "dataset_version": "2.0",
  "embedding_model": "...",
  "reranker_model": "...",
  "rerank_applied": true,
  "chunk_size": 200,
  "metrics": {},
  "latency_p50_ms": 0,
  "latency_p95_ms": 0
}
```

README 只能展示这套新结果。旧报告保留历史，但顶部标记 `legacy` 和“不用于当前质量结论”。

### 8.5 第五阶段提交

```text
test: promote qrels v2 to the canonical RAG evaluation pipeline
```

### 8.6 Gate 5

- [ ] 正式 runner 不再调用旧指标。
- [ ] 所有比例指标位于 `[0,1]`。
- [ ] Rerank 启用组确认模型实际参与。
- [ ] 新结果可从空评测库重复生成。
- [ ] README 已更新，旧数字明确归档。

---

## 9. Phase 6：完整测试与 Warning 清零

### 9.1 未 await warning

仍有 `AsyncClient.get was never awaited`。检查 Web Search 测试对 `httpx.AsyncClient.get` 的 patch，统一使用 `AsyncMock`：

```python
mock_get = AsyncMock(return_value=response)
```

同时保证 mock context manager 的 `__aenter__` 返回正确实例。

修复后启用：

```ini
filterwarnings =
    error::RuntimeWarning
```

第三方 PyMuPDF/SWIG DeprecationWarning 使用精确模块过滤，不得全局忽略。

### 9.2 完整验证矩阵

```bash
# 静态检查
ruff check backend --config pyproject.toml
python -m mypy backend --config-file pyproject.toml

# 后端
cd backend
pytest tests/ -W error::RuntimeWarning \
  --cov=. \
  --cov-report=term-missing \
  --cov-fail-under=70

# 前端
cd ../frontend
npm ci
npm run lint
npm test
npm run build

# 最小安装
pip install -r backend/requirements.txt
pytest backend/tests/test_optional_components.py -v

# Docker
docker compose up -d --build
# 执行完整 smoke
docker compose down -v
```

### 9.3 第六阶段提交

```text
test: make the full release verification matrix pass cleanly
```

### 9.4 Gate 6

- [ ] 后端 0 failure、0 error。
- [ ] 项目自身 RuntimeWarning 为 0。
- [ ] Ruff/Mypy 0 error。
- [ ] 前端 lint/test/build 全绿。
- [ ] 覆盖率达到门槛。
- [ ] 最小安装和 Compose smoke 全绿。

---

## 10. Phase 7：整理提交并合并 worktree

### 10.1 Worktree 提交结构

最终提交建议保持以下主题边界：

1. `style: make security hardening pass static quality gates`
2. `feat: complete admin-token authentication across frontend and backend`
3. `refactor: decouple ingestion targets for staged index builds`
4. `feat: make backup restore atomic across database and indexes`
5. `fix: make compose networking and frontend proxy work end to end`
6. `test: promote qrels v2 to the canonical RAG evaluation pipeline`
7. `test: make the full release verification matrix pass cleanly`

禁止把 800 行以上改动压成一个无法审查的提交。

### 10.2 合并前检查

```bash
git status --short
git log --oneline --decorate -10
git diff <base>...HEAD --stat
git diff <base>...HEAD --check
```

确保所有新增文件已跟踪，尤其是：

- `maintenance.py`
- `metrics.py`
- `security.py`
- `utils/safe_archive.py`
- 安全测试和 qrels v2 文件

### 10.3 根工作区重叠修改处理

根工作区原有修改不能直接丢弃。推荐流程：

1. 把根工作区现有修改提交到独立保护分支。
2. 从主分支创建 integration 分支。
3. 合并安全加固 worktree 分支。
4. 再合并根工作区保护分支。
5. 对重叠文件逐段人工合并，不使用整文件覆盖。
6. 在 integration 分支重新执行 Gate 6 全套验证。

重点冲突文件：

- `backend/main.py`
- `backend/api/chat.py`
- `backend/memory/profile.py`
- `backend/tests/conftest.py`
- `backend/requirements-dev.txt`
- `README.md`

### 10.4 合并后的验证

不能只依赖 worktree 的测试结果。合并后的根项目必须重新：

- 从无 `data/`、无 `.env` 环境运行测试。
- 运行最小安装启动。
- 运行 Compose smoke。
- 运行安全归档测试。
- 运行 qrels v2 正式评测。

### 10.5 Gate 7

- [ ] worktree 工作区干净。
- [ ] 根工作区改动已安全合并，无丢失。
- [ ] integration 分支完整 CI 全绿。
- [ ] 合并后项目根目录包含全部新增模块。
- [ ] 文档中的命令和默认值与实际代码一致。

---

## 11. Phase 8：发布候选验收

### 11.1 功能验收

- [ ] 文本 PDF、Word、Excel、TXT 正常入库。
- [ ] OCR 缺失时文本类文档正常，扫描件明确降级或失败。
- [ ] Rerank 缺失时基础检索正常，状态明确。
- [ ] 管理令牌可以完成前端登录、聊天、上传、设置和备份。
- [ ] 令牌错误时前端能正确重新认证。

### 11.2 安全验收

- [ ] 路径穿越、链接、设备文件和压缩炸弹被拒绝。
- [ ] 敏感接口全部认证。
- [ ] Token 不出现在 URL、日志和 bundle。
- [ ] Qdrant 默认不暴露宿主机。
- [ ] 默认只能通过 localhost 访问。

### 11.3 一致性验收

- [ ] 恢复前后 Document ID 一致。
- [ ] SQLite/Qdrant/BM25 chunk 数一致。
- [ ] 恢复故障可以完整回滚。
- [ ] 中途进程退出后旧数据仍可用。

### 11.4 质量验收

- [ ] 完整测试全绿。
- [ ] 静态门禁全绿。
- [ ] qrels v2 指标可信。
- [ ] 新 README 指标可重复生成。
- [ ] P95 检索延迟和完整模型资源占用有记录。

---

## 12. 最终完成定义

本流程完成必须同时满足：

> 优化代码已经从独立 worktree 安全合并到项目根分支；前端与后端认证完整闭环；备份恢复在 SQLite、Qdrant、BM25 和 uploads 四层保持原子一致；Docker Compose 可以真实运行；旧评测已迁移到 qrels v2；完整 CI 无失败且项目自身 RuntimeWarning 为零。

达到该状态后，项目才适合标记为安全加固阶段完成，并进入下一轮真实负载、长期稳定性和用户体验优化。

