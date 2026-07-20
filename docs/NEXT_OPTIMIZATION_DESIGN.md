# 下一轮开源级优化设计

## 1. 背景

上一轮优化后，项目已经补齐了 `.env.example`、依赖拆分、WebSearch 默认关闭、embedding 维度懒加载、BM25 df 统计修复、参数化 SQL、前端 CI、Docker 前端访问地址、配置文档和故障排查文档。整体已经从“可展示 Beta”推进到“接近可开源 Beta”。

本轮复评仍发现 5 个会影响开源发布质量的问题：

1. `test_docker_compose.py` 使用 `yaml`，但 `requirements-dev.txt` 未声明 `PyYAML`。
2. `tests/conftest.py` 的 autouse fixture 会让纯静态测试也强依赖 `qdrant_client`。
3. `main.py` 启动阶段仍同步执行 `memory.profile.rebuild_index()`，无 key / 无 Qdrant 场景仍存在副作用。
4. `tool_result_json` 已保存，但聊天历史重放时没有使用，下一轮追问仍只看到 `Success: n results`。
5. README 中 `WEB_SEARCH_ENABLED` 默认值仍写 `true`，与 `config.py` / `.env.example` 的 `false` 不一致。

本文档给出下一轮优化方案，用于把项目推进到更稳的开源 Beta 状态。

## 2. 优化目标

本轮目标不是新增功能，而是补齐开源发布前的工程闭环：

- 干净 CI 环境下依赖完整，不因缺少测试依赖失败。
- 静态测试、单元测试、集成测试隔离清晰。
- 无 API key / 无外部依赖时，服务启动路径保持低副作用。
- 多轮 Agent 对话能真正复用上一轮工具结果。
- README 与实际配置保持一致。

完成后，开源就绪度目标提升到 8/10 左右。

## 3. 优先级与范围

### P0：必须修

- 补齐 `PyYAML` 到 `requirements-dev.txt`。
- 拆分 pytest autouse fixture，避免所有测试都强制初始化 DB / Qdrant。
- 历史重放时优先使用 `tool_result_json`。
- 修正 README 中 `WEB_SEARCH_ENABLED` 默认值。

### P1：建议本轮修

- 将启动期 `rebuild_index()` 改为后台任务。
- 给 `tool_result_json` 历史重放增加单元测试。
- 给无需 DB 的测试加 marker 或目录隔离。

### P2：可后续继续

- CI 增加 smoke 级 docker compose build 检查。
- 将前端 TypeScript build info 输出目录调整到可写且不污染 `node_modules`。
- 将旧文档中的已解决问题标记为 completed，避免文档互相矛盾。

## 4. 方案一：补齐 CI 测试依赖

### 问题

`backend/tests/test_docker_compose.py` 使用：

```python
import yaml
```

但 `backend/requirements-dev.txt` 当前没有 `PyYAML`。CI 中只安装：

```bash
pip install -r requirements.txt
pip install -r requirements-dev.txt
```

因此干净环境下后端测试会因 `ModuleNotFoundError: No module named 'yaml'` 失败。

### 设计

在 `backend/requirements-dev.txt` 中增加：

```txt
PyYAML>=6.0
```

同时更新 `backend/tests/test_requirements.py`，把 `pyyaml` 纳入 dev 依赖校验：

```python
required = ["pytest", "pytest-asyncio", "pytest-cov", "mypy", "ruff", "httpx", "pyyaml"]
```

### 验收

- 干净环境执行 `pip install -r backend/requirements.txt -r backend/requirements-dev.txt` 后可 import `yaml`。
- `test_docker_compose.py` 不再因缺依赖失败。

## 5. 方案二：拆分 pytest fixture，隔离静态测试

### 问题

`backend/tests/conftest.py` 中的 `setup_db` 是 autouse fixture。它会在每个测试前执行：

```python
from vectordb.qdrant import reset_client_for_test
```

这导致纯静态测试也必须安装并导入 `qdrant_client`，例如：

- `test_env_example_sync.py`
- `test_requirements.py`
- `test_docker_compose.py`

这些测试本质上不需要数据库、Qdrant 或 embedding。

### 设计

将当前 autouse fixture 改为显式 fixture 或 marker 驱动。

推荐方案：

1. `setup_db` 不再 `autouse=True`。
2. 新增 marker：`@pytest.mark.db`。
3. 在 `pytest_collection_modifyitems` 中，给需要数据库的测试自动挂 fixture，或要求测试显式声明参数。

简单实现方式：

```python
@pytest_asyncio.fixture
async def setup_db():
    ...

@pytest.fixture(autouse=True)
def _auto_db_fixture(request):
    if request.node.get_closest_marker("db"):
        return request.getfixturevalue("setup_db")
```

更清晰的实现方式是让需要 DB 的测试显式写：

```python
@pytest.mark.asyncio
async def test_xxx(setup_db):
    ...
```

考虑项目测试数量较多，建议采用 marker 过渡方案：

- 默认不初始化 DB。
- 需要 DB 的测试文件或测试类加 `pytestmark = pytest.mark.db`。
- 纯静态测试不加 marker。

### 需要标记为 db 的测试范围

初步建议给以下目录或文件加 marker：

- `backend/tests/api/`
- `backend/tests/agent/`
- `backend/tests/memory/`
- `backend/tests/rag/test_pipeline.py`
- `backend/tests/rag/test_retriever*.py`
- `backend/tests/textdb/`
- `backend/tests/vectordb/`
- `backend/tests/storage/`
- `backend/tests/test_e2e_mvp.py`

不需要 DB 的测试：

- `backend/tests/test_env_example_sync.py`
- `backend/tests/test_requirements.py`
- `backend/tests/test_docker_compose.py`
- 纯 splitter/tokenizer/loader 的单元测试可视具体依赖决定。

### 验收

- 只运行静态测试时，不需要 import `qdrant_client`：

```bash
pytest tests/test_env_example_sync.py tests/test_requirements.py tests/test_docker_compose.py -q
```

- 全量测试在安装完整 runtime + dev 依赖后仍通过。
- CI 不因 fixture 误触发外部依赖而失败。

## 6. 方案三：启动期 memory rebuild 改为后台低副作用任务

### 问题

`backend/main.py` lifespan 中仍有：

```python
from memory.profile import rebuild_index
await rebuild_index()
```

虽然 `rebuild_index()` 内部会捕获部分异常，但启动阶段仍同步进入 memory embedding / Qdrant 相关路径。开源项目更理想的行为是：

- `/api/health` 尽快可用。
- 外部依赖不可用时不阻塞启动。
- 后台索引失败只影响 recall_memory，不影响基础服务。

### 设计

将同步调用改为后台任务：

```python
from memory.profile import rebuild_index
from worker.tasks import get_task_manager

if settings.memory_enabled:
    get_task_manager().create(
        rebuild_index(),
        "memory_rebuild_index",
        metadata={"startup": True},
    )
```

同时调整 `rebuild_index()`：

- 当 profile 为空时直接返回，不访问 embedding。
- 当缺少 embedding key 时记录 info，跳过语义索引。
- 保留 direct match fallback。

可新增辅助函数：

```python
def embedding_configured() -> bool:
    return bool(settings.embedding_api_key or settings.llm_api_key)
```

### 验收

- 无 API key 时启动服务，`/api/health` 返回 200。
- `/api/health/tasks` 可看到 `memory_rebuild_index` completed / skipped / failed 记录。
- 记忆索引失败不会影响聊天、文档列表、配置页。

## 7. 方案四：tool_result_json 历史重放闭环

### 问题

项目已经在 `Message` 表中增加 `tool_result_json`，并在保存工具消息时写入：

```python
tool_result_json=_truncate_tool_result(result_data)
```

但构造历史时仍使用：

```python
ChatMessage(role="tool", content=m.content, ...)
```

这意味着下一轮 LLM 看到的仍是：

```txt
Success: 8 results
```

而不是真实检索内容。保存字段没有真正提升多轮 RAG 连贯性。

### 设计

历史重放时优先使用 `tool_result_json`：

```python
tool_content = m.tool_result_json or m.content
history.append(ChatMessage(
    role="tool",
    content=tool_content,
    tool_call_id=m.tool_call_id,
    tool_name=m.tool_name,
))
```

为了避免上下文过大，当前 `_truncate_tool_result()` 已限制 top 3 和文本长度，可以继续使用。

建议进一步加一层格式包装，让 LLM 知道这是历史工具结果：

```python
tool_content = m.tool_result_json or m.content
if m.tool_result_json:
    tool_content = f"[历史工具结果: {m.tool_name}]\n{tool_content}"
```

### 测试设计

新增测试：

1. 保存 tool result 后，数据库中 `tool_result_json` 非空。
2. 构造历史时，tool message content 使用 `tool_result_json`。
3. 没有 `tool_result_json` 的旧消息仍 fallback 到 `content`。

建议抽出一个纯函数，降低测试难度：

```python
def _tool_message_content(m: Message) -> str:
    if m.tool_result_json:
        return f"[历史工具结果: {m.tool_name}]\n{m.tool_result_json}"
    return m.content or ""
```

### 验收

- 多轮追问时，下一轮上下文包含上一轮 top 检索结果。
- 旧数据库消息不会因为缺少 `tool_result_json` 出错。
- 单条历史 tool 内容有最大长度限制。

## 8. 方案五：README 配置漂移修正

### 问题

`README.md` 中 `WEB_SEARCH_ENABLED` 默认值仍写 `true`，但实际：

- `backend/config.py` 默认 `false`
- `backend/.env.example` 默认 `false`

### 设计

将 README 表格中的默认值改为：

```md
| `WEB_SEARCH_ENABLED` | 启用联网搜索 | `false` |
```

同时检查英文部分是否也有同样漂移。

### 验收

- README、`.env.example`、`config.py` 三者默认值一致。
- `rg "WEB_SEARCH_ENABLED"` 不再出现互相矛盾的默认值说明。

## 9. 建议执行顺序

### Step 1：修依赖和 README

低风险、收益快。

- `requirements-dev.txt` 加 `PyYAML>=6.0`
- `test_requirements.py` 加 pyyaml 校验
- README `WEB_SEARCH_ENABLED` 默认值改为 `false`

### Step 2：修 pytest fixture 隔离

这是保证 CI 可信的关键。

- 移除全局 autouse DB 初始化
- 给需要 DB 的测试加 marker 或显式 fixture
- 先跑静态测试，再跑全量测试

### Step 3：修 tool_result_json 重放

这是 Agent 质量闭环。

- 抽 `_tool_message_content()`
- 历史构造使用 `tool_result_json`
- 增加 fallback 测试

### Step 4：启动 memory rebuild 后台化

这是启动降级闭环。

- `main.py` 改为 task manager 后台执行
- `rebuild_index()` 增加 skip 条件
- 测试无 key health 可用

## 10. 验收命令

推荐在完成后执行：

```bash
cd backend
python -m pip install -r requirements.txt -r requirements-dev.txt
python -m pytest tests/test_env_example_sync.py tests/test_requirements.py tests/test_docker_compose.py -q
python -m pytest tests/api/test_chat.py tests/test_startup_degraded.py tests/worker/test_tasks.py -q
python -m pytest tests/ -q
python -m ruff check . --config ../pyproject.toml
python -m mypy . --config-file ../pyproject.toml
```

前端：

```bash
cd frontend
npm ci
npm run build
npm test
```

如果在 Windows PowerShell 遇到执行策略问题，可使用：

```bash
npm.cmd run build
npm.cmd test
```

## 11. 完成标准

- [ ] `PyYAML` 已加入 dev 依赖。
- [ ] 静态测试不再依赖 `qdrant_client`。
- [ ] 全量测试仍能初始化 DB / Qdrant 测试环境。
- [ ] `tool_result_json` 被用于下一轮历史重放。
- [ ] 启动期 memory rebuild 不阻塞 `/api/health`。
- [ ] README 中 `WEB_SEARCH_ENABLED` 默认值为 `false`。
- [ ] CI 后端和前端 job 在干净环境中通过。

完成以上项目后，可以把当前项目定位提升为“开源 Beta 级”，后续再投入 RAG 评测自动化、Docker smoke test 和发布流程即可。
