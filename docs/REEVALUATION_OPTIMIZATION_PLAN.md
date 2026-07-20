# RAG Agent 二次复评专项优化方案

> 日期：2026-07-10  
> 当前复评分：7.3 / 10  
> 目标评分：8.3～8.6 / 10  
> 适用项目：`D:\Python\subject1\RAG_Agent`

---

## 1. 文档目的

本方案基于二次代码复评和隔离环境实测，聚焦目前真正阻止项目继续提分的工程问题。它不是新增功能路线图，而是一份可以直接拆成开发任务的修复方案。

本轮只处理以下五条主线：

1. 让 Rerank 和 OCR 成为真正可拔插的可选能力。
2. 让全新 clone 在没有 `data/`、缓存和本地模型时通过测试。
3. 清零 Ruff、Mypy 和项目自身 RuntimeWarning，使 CI 全绿。
4. 修正 Recall/NDCG 等 RAG 评测指标，建立可信基线。
5. 修复备份恢复安全风险，并给敏感 API 增加访问控制。

完成本方案后，项目应从“功能完整的开源 Beta”进入“默认安全、验证可信的生产候选 Beta”。

---

## 2. 二次复评基线

### 2.1 已确认的有效优化

- `WEB_SEARCH_ENABLED` 文档默认值已修正为 `false`。
- `PyYAML` 已加入开发依赖。
- 用户画像索引重建已改为后台任务。
- 无 Embedding Key 时，画像索引重建会主动跳过。
- 历史对话会优先恢复完整 `tool_result_json`。
- 数据库 fixture 已取消全局 autouse，减少无关测试副作用。
- 前端构建和 20 项组件测试保持通过。

### 2.2 当前实测结果

| 验证项 | 当前结果 |
|---|---:|
| 后端测试收集 | 335 项 |
| 干净副本直接执行 | 22 passed / 8 skipped / 305 errors |
| 手动创建 `backend/data` 后 | 323 passed / 12 skipped |
| Pytest warning | 8 个，其中 3 个 RuntimeWarning |
| Ruff | 7 个错误 |
| Mypy | 1 个错误 |
| 前端测试 | 20 / 20 passed |
| 前端构建 | 通过，存在 CSS at-rule warning |
| 缺少 `sentence-transformers` 的最小安装 | FastAPI lifespan 启动失败 |

### 2.3 发布阻断项

- 最小安装无法保证启动。
- 全新 clone 无法直接运行完整测试。
- 当前 GitHub Actions 会被 Ruff、Mypy 或测试目录问题阻断。
- 检索评测结果中 Recall 可能超过 100%。
- 备份恢复仍直接调用不安全的 `tar.extractall()`。
- 设置、清空、备份和恢复等敏感接口没有认证。

---

## 3. 实施顺序

必须按照以下顺序实施：

```text
Phase 1  可选依赖与干净启动
  ↓
Phase 2  测试隔离与 CI 全绿
  ↓
Phase 3  可信 RAG 评测
  ↓
Phase 4  备份安全与 API 认证
  ↓
Phase 5  完整回归、文档和发布门槛
```

原因：先让最小环境和 CI 稳定，后续安全与评测改动才能获得可靠的自动化反馈。

---

## 4. Phase 1：真正的可选 Rerank 与 OCR

### 4.1 当前根因

`backend/reranker/factory.py` 顶层导入：

```python
from .cross_encoder import CrossEncoderReranker
```

`backend/reranker/cross_encoder.py` 顶层导入：

```python
from sentence_transformers import CrossEncoder
```

因此即使 `RERANK_ENABLED=false`，FastAPI lifespan 导入 `reranker.factory` 时仍要求安装 `sentence-transformers`。这与“最小安装不含 Rerank 依赖也能启动”的设计目标冲突。

### 4.2 Rerank 工厂重构

`factory.py` 不得导入任何可选第三方库。建议实现状态机：

```python
from enum import StrEnum


class ComponentStatus(StrEnum):
    DISABLED = "disabled"
    MISSING_DEPENDENCY = "missing_dependency"
    LOADING = "loading"
    READY = "ready"
    FAILED = "failed"
```

推荐结构：

```python
_reranker = None
_status = ComponentStatus.DISABLED
_last_error = ""


def create_reranker():
    global _reranker, _status, _last_error

    if not settings.rerank_enabled:
        _status = ComponentStatus.DISABLED
        return None

    if _reranker is not None:
        return _reranker

    try:
        from .cross_encoder import CrossEncoderReranker
    except ImportError as exc:
        _status = ComponentStatus.MISSING_DEPENDENCY
        _last_error = str(exc)
        logger.warning("reranker dependency unavailable")
        return None

    _reranker = CrossEncoderReranker(settings.rerank_model)
    return _reranker
```

`cross_encoder.py` 也应将 `sentence_transformers` 移入模型加载线程，避免导入模块即加载可选依赖：

```python
def _load():
    try:
        from sentence_transformers import CrossEncoder
        self._model = CrossEncoder(self._model_name)
    ...
```

### 4.3 Rerank 结果透明度

当前检索层用 `use_rerank and settings.rerank_enabled` 表示“执行了重排”，但模型未 ready 时只返回中性分数。这会混淆“用户请求重排”和“重排实际生效”。

返回结果应区分：

```json
{
  "rerank_requested": true,
  "rerank_applied": false,
  "rerank_status": "loading",
  "rerank_model": "BAAI/bge-reranker-v2-m3"
}
```

状态规则：

| 配置和运行状态 | `requested` | `applied` | `status` |
|---|:---:|:---:|---|
| 配置关闭 | false | false | disabled |
| 配置开启但依赖缺失 | true | false | missing_dependency |
| 正在下载或加载 | true | false | loading |
| 模型加载失败 | true | false | failed |
| 模型成功参与排序 | true | true | ready |

日志与评测结果必须使用 `rerank_applied`，不能只看配置开关。

### 4.4 OCR 降级透明度

OCR 的 DocTR 导入已经在后台线程内，主服务不会因模型加载失败直接退出，但仍需补齐：

- `disabled/loading/ready/failed/missing_dependency` 状态。
- 最近一次失败的脱敏错误摘要。
- 扫描 PDF 或图片在 OCR 不可用时不能静默入库为空。
- 纯文本 PDF、Word、Excel、TXT 不受 OCR 状态影响。

建议文档状态扩展：

```text
ready       正常完成
degraded    文档已入库，但部分图片或扫描页未识别
failed      文档没有可用文本，无法入库
```

如果不希望修改数据库枚举，也至少在 `error_message` 或 ingestion metadata 中记录 OCR 降级原因，并在前端明确提示。

### 4.5 健康接口

扩展 `/api/health/dependencies`：

```json
{
  "status": "degraded",
  "dependencies": {
    "sqlite": "ok",
    "qdrant": "ok",
    "embedding": "missing_api_key",
    "llm": "missing_api_key",
    "reranker": "disabled",
    "ocr": "loading"
  }
}
```

可选组件 `disabled` 不应让总体状态变成 error；用户明确开启但加载失败时，总体状态为 degraded。

### 4.6 测试矩阵

新增 `backend/tests/test_optional_components.py`：

- 未安装 `sentence-transformers` 且 Rerank 关闭，服务启动成功。
- 未安装 `sentence-transformers` 且 Rerank 开启，服务降级启动。
- Rerank loading 时检索可用，`rerank_applied=false`。
- Rerank ready 时才返回 `rerank_applied=true`。
- 未安装 DocTR 时服务启动成功。
- OCR 不可用时文本 PDF 正常入库。
- OCR 不可用时扫描 PDF 或图片返回 degraded/failed，而不是假 ready。

### 4.7 验收标准

- 只安装 `requirements.txt` 可以启动后端。
- Rerank/OCR 可选依赖缺失不导致进程退出。
- 健康接口和前端显示真实组件状态。
- 最小安装不自动下载本地模型。

---

## 5. Phase 2：干净环境测试与 CI 全绿

### 5.1 测试目录问题

当前 `tests/conftest.py` 使用相对路径：

```python
os.environ["DATABASE_URL"] = "sqlite+aiosqlite:///./data/test_rag_agent.db"
```

但 `init_db()` 不会创建 SQLite 父目录。新 clone 没有被 Git 跟踪的空 `data/`，因此大量测试在 fixture 初始化时失败。

### 5.2 推荐实现

不要继续依赖仓库内 `data/`。使用 session 级临时目录，并确保在应用模块导入前设置环境变量。

推荐在 `conftest.py` 最顶部：

```python
import os
import shutil
import tempfile
from pathlib import Path


_TEST_ROOT = Path(tempfile.mkdtemp(prefix="rag_agent_tests_"))
(_TEST_ROOT / "uploads").mkdir(parents=True, exist_ok=True)
(_TEST_ROOT / "qdrant").mkdir(parents=True, exist_ok=True)

os.environ["DATABASE_URL"] = (
    f"sqlite+aiosqlite:///{(_TEST_ROOT / 'test.db').as_posix()}"
)
os.environ["QDRANT_PATH"] = str(_TEST_ROOT / "qdrant")
os.environ["UPLOAD_DIR"] = str(_TEST_ROOT / "uploads")
os.environ["SECRET_KEY"] = "test-only-secret-key"
```

通过 session fixture 或 `pytest_sessionfinish` 清理 `_TEST_ROOT`。

更长期的方案是重构 `models.database`，通过 fixture 创建独立 engine，避免 module-level engine 与环境变量导入顺序耦合。

### 5.3 Fixture 分类

建议按真实依赖拆分 marker：

```text
unit            纯逻辑，不初始化数据库
db              需要 SQLite
vector          需要 Qdrant local
model           需要本地 OCR/Rerank 模型
external_api    需要真实 LLM/Embedding API
slow            执行时间较长
```

不要简单按目录把全部 `agent/`、`rag/` 测试都强制挂载数据库。有些分类器、splitter、RRF 和 Calculator 测试是纯逻辑，应保持快速、无副作用。

### 5.4 Ruff 修复清单

当前 7 项：

1. `tests/test_docker_compose.py` import 排序。
2. `tests/test_docker_compose.py` 替换 `assert False`。
3. `tests/test_requirements.py` import 排序。
4. `tests/test_startup_degraded.py` 顶部 import 排序。
5. `tests/test_startup_degraded.py` 函数内 import 排序。
6. `tests/textdb/test_bm25_search.py` 删除未使用的 `df2`，或恢复实际断言。
7. `tests/worker/test_tasks.py` import 排序。

先执行：

```bash
ruff check backend --config pyproject.toml --fix
```

然后人工处理 `assert False` 和 `df2`。不应仅把规则加入 ignore。

### 5.5 Mypy 修复

`backend/rag/query_classifier.py` 的 `ClassVar` 只能用于类体赋值。根据变量语义选择：

- 如果是类常量：移动到 class body。
- 如果是模块级映射：去掉 `ClassVar`，使用普通类型注解。

修复后运行：

```bash
python -m mypy backend --config-file pyproject.toml
```

### 5.6 RuntimeWarning 修复

Web Search 测试当前有 `AsyncClient.get was never awaited`。通常是将异步方法 mock 成普通返回值，或 patch 目标层级错误。

统一使用：

```python
mock_get = AsyncMock(return_value=response)
```

并确保 patch 的是代码实际实例化后调用的方法。修复后在 pytest 配置中增加：

```ini
filterwarnings =
    error::RuntimeWarning
```

PyMuPDF/SWIG 的第三方 DeprecationWarning 可以按模块精确忽略，禁止全局忽略所有 DeprecationWarning。

### 5.7 前端 warning

- 将 `ConfirmDialog.tsx` 中非组件导出移到独立文件。
- 核对 Tailwind 4 与 Vite 插件配置，消除 Lightning CSS 对 `@theme`、`@tailwind` 的 warning。
- `npm run lint`、`npm test`、`npm run build` 应当均为无 warning 通过。

### 5.8 CI 任务拆分

```text
backend-static
  ruff + mypy

backend-unit
  无 DB、无模型、无网络

backend-integration
  SQLite + Qdrant local

minimal-startup
  仅 requirements.txt，不安装 OCR/Rerank

frontend-quality
  npm ci + lint + test + build

security
  backup security tests + dependency scan

docker-smoke
  build + up + health
```

### 5.9 Phase 2 验收命令

```bash
ruff check backend --config pyproject.toml
python -m mypy backend --config-file pyproject.toml
pytest backend/tests -W error::RuntimeWarning --cov=backend --cov-fail-under=70

cd frontend
npm ci
npm run lint
npm test
npm run build
```

验收必须在没有 `.env`、`data/`、缓存目录和模型权重的全新 checkout 中执行。

---

## 6. Phase 3：修正 RAG 评测体系

### 6.1 当前指标错误

当前逻辑把每个检索文本是否匹配任意 ground truth 转换为 0/1，再计算：

```python
recall = sum(top_k) / len(ground_truth_texts)
```

如果三个重复或重叠 chunk 都匹配同一个 ground truth，就会被计为命中三次，导致 Recall 超过 1。

NDCG 的 IDCG 又来自当前已经检索到的 relevance 排序，会高估未完整召回时的结果。

### 6.2 短期修复

短期保留文本 ground truth，但建立一对一匹配：

```python
matched_gt: set[int] = set()
relevance: list[int] = []

for chunk in retrieved_chunks:
    match = best_unmatched_ground_truth(chunk, ground_truth_texts, matched_gt)
    if match is None:
        relevance.append(0)
    else:
        matched_gt.add(match)
        relevance.append(1)

recall_at_k = len(matched_gt_in_top_k) / len(ground_truth_texts)
```

同时增加硬断言：

```python
assert 0.0 <= metric <= 1.0
```

IDCG 使用完整 ground truth 数：

```python
ideal_count = min(k, len(ground_truth_texts))
idcg = sum(1 / log2(i + 2) for i in range(ideal_count))
```

### 6.3 长期 qrels 设计

文本相似度只能作为辅助判定。长期应给文档段落稳定标识：

```json
{
  "query_id": "paygate-error-001",
  "query": "ERR_40003 是什么问题",
  "relevant": [
    {
      "document_key": "paygate-api-v3",
      "section_key": "errors/ERR_40003",
      "grade": 3
    }
  ],
  "expected_facts": [
    "amount exceeds one million"
  ],
  "answerable": true
}
```

chunk metadata 保存 `document_key`、标题路径和 `section_key`。同一 section 的多个重叠 chunk 只能贡献一次 Recall。

### 6.4 数据集拆分

- `dev`：用于调 chunk、RRF 权重和阈值。
- `test`：发布前运行，不参与调参。
- `challenge`：跨文档、多跳、版本冲突、无答案和 prompt injection。

第一阶段至少 100 条查询：

- 精确错误码、SKU、条款、日期和数值。
- 中文、中英混合、同义改写。
- 跨文档比较、多跳问题。
- 文档中不存在答案的问题。
- 内容相似但版本不同的冲突文档。

### 6.5 Rerank 评测规则

评测报告必须记录：

```json
{
  "rerank_requested": true,
  "rerank_applied": true,
  "rerank_status": "ready",
  "rerank_model": "...",
  "model_revision": "..."
}
```

如果模型未 ready，则该轮不能计入“启用 Rerank”的对比结果。

对比至少输出：

- 无 Rerank 的 P@5、MRR、NDCG、P95。
- 有 Rerank 的相同指标。
- 绝对增益、相对增益和增加的延迟。
- 排序发生变化的查询比例。

### 6.6 答案级评测

新增：

- Answer correctness。
- Faithfulness。
- Citation precision / recall。
- 无答案拒答率。
- Tool selection accuracy。
- 端到端任务成功率。
- P50/P95 延迟和 token 成本。

### 6.7 Phase 3 验收标准

- 所有比例指标位于 `[0,1]`。
- 重复 chunk 不会重复贡献 Recall。
- Rerank 未真实加载时不会报告“已重排”。
- 固定命令可以从空评测库重建数据并复现 README 指标。
- 每份结果保存 commit、配置、模型、数据集版本和时间戳。

---

## 7. Phase 4：备份安全与 API 认证

### 7.1 安全解压

禁止直接：

```python
tar.extractall(restore_dir)
```

新增 `backend/utils/safe_archive.py`，逐个验证成员：

- 拒绝绝对路径和 `..` 路径跳转。
- 规范化目标路径后确认仍位于恢复目录内。
- 拒绝 symlink、hardlink、device、FIFO。
- 只允许普通文件和目录。
- 限制上传大小、成员数量、单成员大小和总解压大小。
- 使用 Python 3.12 `filter="data"` 作为第二道防线。

建议默认限制：

```env
BACKUP_MAX_UPLOAD_MB=512
BACKUP_MAX_EXTRACTED_MB=2048
BACKUP_MAX_MEMBERS=10000
```

上传采用分块读取，不能一次性 `await file.read()` 到内存。

### 7.2 备份临时文件清理

创建备份后，`FileResponse` 完成并不会自动保证临时目录删除。使用 `BackgroundTask`：

```python
return FileResponse(
    tar_path,
    ...,
    background=BackgroundTask(shutil.rmtree, backup_dir, True),
)
```

同时覆盖客户端中途断开场景。

### 7.3 认证策略

项目是单用户本地知识库，不需要立即引入完整 RBAC。采用管理令牌即可：

```env
ADMIN_API_TOKEN=
SERVER_HOST=127.0.0.1
ALLOW_REMOTE_ACCESS=false
```

新增：

```python
async def require_admin(
    token: str | None = Security(APIKeyHeader(name="X-Admin-Token")),
):
    ...
```

优先保护：

- 设置更新与连接测试。
- collection 重建和清空全部数据。
- 备份下载与恢复。
- 文档上传、删除和清空。
- 记忆与会话删除。

使用 `secrets.compare_digest()`，令牌不能出现在 URL、日志和错误响应中。

### 7.4 网络默认值

- 默认绑定 `127.0.0.1`。
- Docker 映射改为 `127.0.0.1:8000:8000` 和 `127.0.0.1:5173:5173`。
- Qdrant 默认不向宿主机映射 6333/6334。
- 只有显式启用远程访问并配置管理令牌时允许 `0.0.0.0`。

### 7.5 恢复一致性

恢复不能先覆盖正式 SQLite 再尝试重建。使用：

```text
安全解压
  → manifest/hash/schema/integrity_check
  → staging SQLite/uploads
  → 临时 Qdrant collection 与 BM25 表重建
  → 获取维护锁
  → 原子切换数据库、文件和索引指针
  → 健康检查
  → 成功后清理旧资源；失败时切回
```

恢复期间写请求返回 503 和 `Retry-After`，同一时间只允许一个恢复或 collection 重建任务。

### 7.6 安全测试

新增 `tests/api/test_backup_security.py`：

- `../outside.txt` 路径穿越。
- Windows 绝对路径和 Unix 绝对路径。
- symlink、hardlink、device、FIFO。
- 超过上传体积和解压体积。
- 成员数量超限。
- 无令牌、错误令牌和正确令牌。
- 恢复各阶段异常后的回滚。
- 临时文件最终清理。

### 7.7 Phase 4 验收标准

- 恶意归档不能写出恢复目录。
- 未认证用户不能读取备份或执行破坏性操作。
- 默认部署不能被局域网直接访问。
- 恢复任意阶段失败不会损坏原数据。

---

## 8. Phase 5：最终回归与发布门槛

### 8.1 必跑矩阵

| 环境 | 安装内容 | 必须结果 |
|---|---|---|
| minimal | runtime requirements | 服务启动、基础 RAG 可用 |
| OCR missing | OCR enabled but dependency absent | 降级启动、状态明确 |
| Rerank missing | Rerank enabled but dependency absent | 降级启动、基础检索可用 |
| full | runtime + OCR + Rerank | 模型加载并真实参与处理 |
| no API key | 无 LLM/Embedding Key | 健康接口 degraded，管理页面可用 |
| Docker | compose 默认配置 | 本机可访问、局域网不可访问 |

### 8.2 发布质量门槛

- Ruff：0 error。
- Mypy：0 error。
- Pytest：0 failure、0 error、0 项目 RuntimeWarning。
- 后端覆盖率：≥ 70%。
- 前端 lint/test/build：全部无 warning 通过。
- 安全归档测试：全部通过。
- 最小安装启动测试：通过。
- Recall、MRR、NDCG 等比例指标：全部在 `[0,1]`。
- README 指标可由固定命令复现。

### 8.3 建议 CI 命令

```bash
# Backend static
ruff check backend --config pyproject.toml
python -m mypy backend --config-file pyproject.toml

# Backend tests
pytest backend/tests \
  -W error::RuntimeWarning \
  --cov=backend \
  --cov-report=term-missing \
  --cov-fail-under=70

# Frontend
cd frontend
npm ci
npm run lint
npm test
npm run build

# Docker smoke
docker compose build
docker compose up -d
curl --fail http://127.0.0.1:8000/api/health
docker compose down
```

---

## 9. 任务拆分建议

| ID | 任务 | 优先级 | 依赖 | 估算 |
|---|---|:---:|---|---:|
| OPT-01 | Rerank 工厂延迟导入 | P0 | 无 | 0.5 天 |
| OPT-02 | OCR/Rerank 状态机与健康接口 | P0 | OPT-01 | 1 天 |
| OPT-03 | 扫描文档 OCR 降级状态 | P1 | OPT-02 | 0.5 天 |
| CI-01 | pytest 临时目录与清理 | P0 | 无 | 0.5 天 |
| CI-02 | Fixture marker 精细拆分 | P1 | CI-01 | 0.5 天 |
| CI-03 | Ruff、Mypy、RuntimeWarning 清零 | P0 | CI-01 | 0.5 天 |
| CI-04 | 最小安装和 Docker smoke job | P1 | OPT-01、CI-03 | 0.5 天 |
| EVAL-01 | Recall/NDCG 短期修复 | P0 | CI-03 | 0.5 天 |
| EVAL-02 | 稳定 qrels 与数据集拆分 | P1 | EVAL-01 | 1.5 天 |
| EVAL-03 | 答案级评测 | P1 | EVAL-02 | 1.5 天 |
| SEC-01 | 安全 tar 解压与资源限制 | P0 | CI-03 | 1 天 |
| SEC-02 | 管理令牌和安全网络默认值 | P0 | CI-03 | 1 天 |
| SEC-03 | staged restore 与回滚 | P1 | SEC-01、SEC-02 | 1.5 天 |

---

## 10. 最终验收清单

### 可选组件

- [ ] 不安装 Rerank 依赖可以启动。
- [ ] 不安装 OCR 依赖可以启动。
- [ ] 用户能看到 disabled/loading/ready/failed 状态。
- [ ] 模型没有实际参与时不会标记 applied。
- [ ] 扫描文档在 OCR 不可用时不会假装成功。

### CI

- [ ] 全新 clone 不需要手动创建 `data/`。
- [ ] Ruff 和 Mypy 全绿。
- [ ] 后端测试无 RuntimeWarning。
- [ ] 前端 lint/test/build 无 warning。
- [ ] 最小安装、完整安装和 Docker 都有自动化验证。

### 评测

- [ ] Recall、MRR、NDCG、Hit Rate 均不超过 1。
- [ ] 重叠 chunk 不会重复贡献 Recall。
- [ ] Rerank 对比只在模型 ready 时计入。
- [ ] 有独立 test/challenge 数据集。
- [ ] 有答案忠实度、引用和拒答指标。

### 安全

- [ ] tar 路径穿越和压缩炸弹测试通过。
- [ ] 敏感接口默认需要认证。
- [ ] 默认仅监听本机。
- [ ] Qdrant 默认不暴露。
- [ ] 恢复失败可以回滚。

---

## 11. 预期评分变化

| 完成阶段 | 预期评分 | 主要提升 |
|---|---:|---|
| 当前 | 7.3 | 功能完整，但 CI、安全和评测有缺口 |
| Phase 1～2 | 7.8～8.0 | 最小安装可信，CI 全绿 |
| Phase 3 | 8.1～8.3 | RAG 指标可信，可用于回归 |
| Phase 4 | 8.3～8.6 | 默认安全、恢复可控 |
| Phase 5 + 真实负载验证 | 8.8+ | 具备生产候选证据 |

10 分不取决于继续堆叠模型或工具数量，而取决于长期真实运行证据，包括稳定性、安全演练、恢复成功率、真实数据集效果和容量边界。

---

## 12. 本轮完成定义

本轮优化完成的最低标准是：

> 在没有 `.env`、`data/`、OCR/Rerank 模型和本地缓存的全新环境中，服务可以降级启动；CI 全绿；检索指标数学正确；备份恢复不能越权写文件；敏感操作默认需要认证。

只有同时满足以上条件，项目才算完成从 7.3 分到 8 分以上的实质升级。

