# RAG Agent 当前工作区实测评估报告

> 评估日期：2026-07-20 08:50:47 +08:00  
> 评估对象：当前未提交工作区，而非仅 Git HEAD  
> Git HEAD：`103c86da3b9e9f145f4033c0378004437db67a98`  
> 分支：`master`  
> 评估方式：源码检查 + 本地命令直接执行 + 机器可读评测产物校验  
> 明确排除：README、`docs/` 中既有结论、人工维护的项目状态说明不作为本报告证据

## 1. 结论摘要

当前项目已经是一个功能和测试资产都较完整的 RAG/Agent 系统，不是普通演示项目。Agent、混合检索、引用校验、修复、缓存、异步入库、鉴权、备份恢复、前端流式交互等主要能力在源码中均有实际实现。

但当前工作区不具备直接发布条件，原因不是历史 RAG 指标不足，而是当前代码批次尚未完成工程收口：

- 最终受控离线测试：`725 passed / 2 failed / 18 skipped / 9 deselected`；
- 生产源码覆盖率：`67%`；CI 当前统计口径为 `74.59%`，因为把测试代码也计入覆盖率；
- Ruff：`70` 个问题；
- Mypy：`4` 个错误；
- 前端 lint、构建、`60/60` 测试全部通过；
- 当前最新历史 RAG 报告的质量和性能指标全部过门禁，但与当前代码相比已有 `3` 个 provenance 指纹失效；
- 默认 release gate 指向更旧的报告，容易再次产生错误结论；
- fresh-start 管理令牌生成路径存在确定的 `ImportError`；
- 数据库方言层的 `rebuild_fts()` 会把当前 BM25 关系表替换为不兼容的 FTS5 表，已在测试顺序中实际触发检索失败。

综合判断：

| 评价对象 | 分数 | 判断 |
|---|---:|---|
| 系统能力与产品完整度 | 8.6/10 | 已达到强原型/内部产品水平 |
| RAG/Agent 设计与历史实测能力 | 9.1/10 | 对应历史指纹版本已超过质量和性能门禁 |
| 当前工作区工程质量 | 7.1/10 | 测试资产强，但存在明确回归和静态门禁失败 |
| 当前工作区发布就绪度 | 5.4/10 | **No-Go**，需先完成 P0/P1 收口 |
| 综合评分 | **7.6/10** | 能力强，当前批次未封板 |

## 2. 证据分级规则

为了防止以后再次混淆，本报告对数据采用三种标记：

- **[DIRECT]**：本次评估在当前工作区直接执行得到；
- **[ARTIFACT]**：来自机器可读 JSON/锁文件等产物，但本次没有重新调用外部模型生成；
- **[CODE]**：直接从当前源码结构、调用关系或配置读取；
- **[NOT VERIFIED]**：本次环境无法或没有执行，不得描述为已通过。

历史评测报告即使指标通过，只要 provenance 与当前代码不一致，就只能证明对应历史版本，不能证明当前工作区。

## 3. 当前基线与工作区状态

### 3.1 版本状态 [DIRECT]

| 项目 | 实测值 |
|---|---:|
| Git HEAD | `103c86da3b9e9f145f4033c0378004437db67a98` |
| 当前分支 | `master` |
| 已修改文件 | 52 |
| 未跟踪路径 | 103 |
| 工作区状态项总数 | 155 |
| 已跟踪 diff | `+2140 / -474`，涉及 52 个文件 |
| `git diff --check` | 失败：2 处 trailing whitespace |

本报告的所有结论只对应上述工作区快照。由于存在大量未提交文件，仅记录 Git HEAD 不足以复现本次结果。

### 3.2 运行环境 [DIRECT]

| 组件 | 版本/状态 |
|---|---|
| OS | Windows |
| Python | 3.12.13 |
| Pytest | 9.1.1 |
| Node.js | 22.19.0 |
| npm | 10.9.3 |
| 前端构建工具 | Vite 8.1.0 |
| Docker Compose 配置解析 | 通过 |
| Docker daemon/E2E | **未验证**；本次环境无法读取 Docker 用户配置/连接 daemon |

项目原 `.venv` 的解释器指向已缺失的 `.python-runtime/python.exe`。本次使用独立 Python 3.12.13，并加载 `.venv/Lib/site-packages` 完成测试。Windows `pywin32.pth` 路径、pytest 临时目录和 tiktoken 缓存均显式固定。

## 4. 源码规模与结构

### 4.1 规模 [DIRECT]

| 指标 | 数值 |
|---|---:|
| Python 文件 | 193 |
| Python 总行数 | 26,532 |
| 前端 TS/TSX 文件 | 46 |
| 前端源码行数 | 4,445 |
| 后端测试文件 | 72 |
| 后端测试代码行数 | 9,188 |
| 前端测试文件 | 9 |
| 前端测试代码行数 | 944 |
| 后端类/函数定义扫描数 | 292 |
| FastAPI 路由/Router 扫描数 | 57 |
| 后端运行依赖声明 | 25 |
| 后端开发依赖声明 | 7 |

主要模块规模：

| 模块 | Python 文件 | 行数 |
|---|---:|---:|
| `agent` | 11 | 4,022 |
| `api` | 11 | 3,024 |
| `rag` | 9 | 1,686 |
| `textdb` | 4 | 668 |
| `memory` | 2 | 477 |
| `worker` | 3 | 377 |
| `models` | 5 | 361 |
| `vectordb` | 4 | 255 |
| `storage` | 3 | 253 |

### 4.2 从源码确认的能力 [CODE]

当前源码实际包含：

- 自定义 Agent loop、工具调用、上下文裁剪、超时预算、流式输出；
- 文档检索、记忆召回、网络搜索、计算器和文档列表工具；
- Qdrant 语义检索与 SQLite BM25 关键词检索；
- RRF 融合、查询分类/改写、去重、质量过滤和可选 rerank；
- grounded-answer verifier、流式原子单元验证、确定性引用修复和有界 LLM 修复；
- 答案缓存及 collection version 失效机制；
- 文档加载、切分、异步入库、重试、generation 状态和恢复；
- SQLite/Alembic、文件存储抽象、备份恢复和 revision gate；
- JWT、RBAC、legacy token 兼容、审计日志和请求日志脱敏；
- Prometheus 指标和 tracing 代码；
- React/Zustand 前端、SSE 对话、文档上传和来源展示。

这部分结论来自实际模块、类、函数和调用关系，不来自项目说明文档。

## 5. 后端直接测试结果

### 5.1 最终受控离线回归 [DIRECT]

测试条件：

- Python 3.12.13；
- 独立可写 TEMP/TMP、数据库、Qdrant 和上传目录；
- `TIKTOKEN_CACHE_DIR` 指向已有 cl100k_base 缓存；
- LLM/Embedding API Key 设置为占位值，使真实网络测试跳过；
- `EMBEDDING_DIM=1536`；
- 排除 `docker` marker；
- 当前工作区全部代码，不回退任何修改。

结果：

| 项目 | 数值 |
|---|---:|
| 收集测试 | 754 |
| 选中执行 | 745 |
| 通过 | 725 |
| 失败 | 2 |
| 跳过 | 18 |
| 排除 Docker 用例 | 9 |
| 已执行非跳过通过率 | 99.72%（725/727） |
| 无覆盖率运行耗时 | 174.69 秒 |
| 覆盖率运行耗时 | 225.00 秒 |
| Pytest warnings | 619 |

两个稳定复现的失败：

1. `tests/test_env_example_sync.py::test_env_example_covers_all_settings`
   - `config.py` 定义了 `RAG_TIMEOUT_RERANK`；
   - `.env.example` 没有该变量；
   - 属于配置样例与代码不同步。

2. `tests/test_ocr_rerank_combinations.py::test_core_retrieval_works_without_ocr_rerank`
   - 向量路径因为占位 API Key按预期不可用；
   - BM25 路径报错：`virtual tables may not be indexed`；
   - 根因是 `models/dialect.py::SqliteAdapter.rebuild_fts()` 删除当前关系型 `bm25_docs`，再创建同名 FTS5 virtual table；
   - 后续 `BM25Search._ensure_tables()` 尝试给 virtual table 创建普通索引而失败；
   - 这不是单纯测试隔离问题，生产环境调用该 rebuild 路径也可能破坏当前 BM25 schema。

### 5.2 覆盖率 [DIRECT]

| 口径 | 覆盖率 | 说明 |
|---|---:|---|
| CI 当前 `--cov=.` 口径 | 74.59% | 包含测试代码，数值会被测试文件自身覆盖率抬高 |
| 排除 `tests/*` 后的生产源码 | **67%** | 8,705 statements，2,833 miss |
| CI 门槛 | 60% | 两种口径均超过门槛，但应改用生产源码口径 |

重点低覆盖模块：

| 模块 | 覆盖率 |
|---|---:|
| `logging_config.py` | 0% |
| `models/fingerprint.py` | 0% |
| `ocr/paddle_ocr.py` | 0% |
| `ocr/preprocess.py` | 0% |
| `rag/query_rewriter.py` | 0% |
| `memory/profile.py` | 25% |
| `api/memories.py` | 26% |
| `api/settings.py` | 27% |
| `ocr/factory.py` | 35% |
| `rag/loaders.py` | 43% |

核心 RAG/Agent 部分相对较好：`verifier.py` 94%、`splitter.py` 98%、`bm25_search.py` 96%、`answer_cache.py` 91%、`retriever.py` 73%、`loop.py` 65%。

### 5.3 静态质量门禁 [DIRECT]

| 门禁 | 结果 |
|---|---|
| Ruff | 失败，70 个问题，其中 59 个可自动修复 |
| Mypy | 失败，4 个错误，分布于 3 个文件 |
| `git diff --check` | 失败，2 处尾随空格 |

Mypy 的 4 个实际错误：

- `api/users.py:103`：`str` 赋给 `UserRole` 字段；
- `api/backup.py:127`：`ScriptDirectory` 未定义；
- `api/backup.py:145`：`ScriptDirectory` 未定义；
- `main.py:66`：`security.generate_admin_token` 不存在。

Ruff 除格式/导入问题外，还包含 `F821` 未定义名称、未使用导入和异常链问题，因此当前后端静态 CI 必然失败。

## 6. 前端直接测试结果

### 6.1 门禁 [DIRECT]

| 项目 | 结果 | 耗时/产物 |
|---|---|---|
| `npm run lint` | 通过 | 1.9 秒 |
| `npm run build` | 通过 | TypeScript + Vite 完成 |
| Vitest | 8/8 文件、60/60 测试通过 | 4.12 秒 |
| `npm ls --depth=0` | 退出码 0 | 存在若干 extraneous WASM/tslib 包 |

生产构建产物：

| 文件 | 原始大小 | gzip |
|---|---:|---:|
| CSS | 30.75 kB | 7.04 kB |
| JS | 454.83 kB | 140.50 kB |

前端当前质量明显好于后端静态质量。JS 单包约 455 kB，尚未触发构建失败，但后续可以考虑路由/组件懒加载。

## 7. RAG 质量与性能：当前事实边界

### 7.1 本次直接验证到的 RAG 测试 [DIRECT]

本次离线回归中以下测试组通过：

- grounded answer gate：13 项；
- quality gate：3 项；
- V4 regression：8 项；
- offline BM25 quality：1 项；
- verifier：39 项；
- grounding repair：22 项；
- stream verification：28 项；
- V4 integration：14 项；
- retriever 单元/质量测试：25 项；
- splitter 与 edge tests：25 项；
- answer cache：14 项。

这证明当前核心算法和规则回归大面积正常，但不能替代一次当前版本的真实在线 93×2 评测。

### 7.2 最新机器评测产物 [ARTIFACT]

机器产物：`backend/tests/grounded_answer_eval_final_full_rescored.json`。

该产物包含 186 条记录，对应 93 个问题的 control/optimized 两组结果。产物记录时间为 `2026-07-17T05:48:07.418957+00:00`。

optimized 聚合指标：

| 指标 | 数值 | 产物内门槛状态 |
|---|---:|---|
| Faithfulness | 100% | 通过 |
| Citation precision | 100% | 通过 |
| Citation recall | 100% | 通过 |
| Abstention accuracy | 100% | 通过 |
| Expected fact recall | 86.995% | 通过，门槛 85% |
| Answer completion accuracy | 100% | 通过 |
| TTFT P50 | 546.25 ms | 通过 |
| TTFT P95 | 683.09 ms | 通过 |
| 总延迟 P50 | 1,142.47 ms | 通过 |
| 总延迟 P95 | 2,067.86 ms | 通过 |
| 总延迟 P99 | 2,421.82 ms | 通过 |
| LLM repair rate | 2.15% | 通过，门槛 10% |

该报告的 `quality_gate.passed` 和 `performance_gate.passed` 都是 `true`。因此可以确认：**对应当时 dataset/verifier/evaluator/prompt 指纹的版本，RAG 已超过全部质量和性能门禁。**

### 7.3 当前版本 provenance 状态 [DIRECT]

本次直接执行：

```text
python release_gate.py --report tests/grounded_answer_eval_final_full_rescored.json
```

结果：失败，但仅剩 3 个 blocker：

- `dataset_sha256` 已变化；
- `verifier_sha256` 已变化；
- `evaluator_sha256` 已变化。

没有质量或性能阈值 blocker。这意味着：

- 不能说历史报告没有过门禁；
- 也不能把历史报告当成当前工作区的有效发布证据；
- 当前工作区必须重新执行完整在线评测，才能恢复可发布 provenance。

### 7.4 默认 release gate 选择错误 [DIRECT + CODE]

直接运行 `python release_gate.py` 时，默认读取的是更旧的 `grounded_answer_eval_v4_final.json`，得到 15 个 blocker，包括旧指标和缺失性能字段。

这正是容易造成误判的根源。默认报告路径必须改为唯一的 canonical release artifact，或由发布流程显式传入并验证报告路径，不能依赖容易过期的硬编码文件名。

### 7.5 本次未验证内容 [NOT VERIFIED]

- 没有重新发起 186 次真实 LLM 在线生成；
- 没有执行 Docker daemon 下的 9 个 Docker/E2E 用例；
- 没有执行 OCR 模型真实推理；
- 没有执行 reranker 模型真实推理；
- 没有做外部并发容量压测。

因此本报告不会声称当前工作区已经重新通过完整在线 release gate。

## 8. 关键缺陷与风险

### P0：fresh-start 启动路径会 ImportError

**证据类型：[DIRECT + CODE]**

`main.py::_bootstrap_admin_token()` 执行：

```python
from security import generate_admin_token
```

但 `security.py` 没有该函数。直接导入实测结果：

```text
ImportError: cannot import name 'generate_admin_token' from 'security'
```

已有 `.env` 且存在 `ADMIN_API_TOKEN` 时该分支被绕过，因此常规测试和已有环境可能掩盖问题；全新部署或令牌为空时会触发。

### P0：数据库方言 rebuild 会破坏当前 BM25 schema

**证据类型：[DIRECT + CODE]**

当前 `BM25Search` 需要三个普通关系表：`bm25_docs`、`bm25_index`、`bm25_stats`。但 `SqliteAdapter.rebuild_fts()` 删除 `bm25_docs` 后创建同名 FTS5 virtual table，字段也与当前 BM25 实现不一致。

后续实际错误：

```text
sqlite3.OperationalError: virtual tables may not be indexed
CREATE INDEX IF NOT EXISTS idx_bm25_docs_did ON bm25_docs(document_id)
```

需要让 dialect rebuild 调用当前 BM25 rebuild 实现，或明确只重建 legacy `chunks_fts`，不能复用 `bm25_docs` 名称。

### P1：当前工作区没有有效的在线发布证据

**证据类型：[DIRECT]**

最新通过门禁的历史报告与当前 dataset/verifier/evaluator 指纹不匹配。必须针对当前工作区重新运行完整在线评测。

### P1：后端 CI 静态门禁失败

**证据类型：[DIRECT]**

Ruff 70 项、Mypy 4 项。CI 当前配置会直接阻止合并/发布。

### P1：固定 bootstrap 管理员密码

**证据类型：[CODE]**

空用户库会创建 `admin/admin123`。即使日志提示修改，仍不适合可远程访问或自动部署环境。应改为：

- 环境变量强制提供初始密码；或
- 生成一次性随机密码，只输出一次；
- 首次登录强制改密；
- 未完成改密前禁止业务 API。

### P1：配置样例与代码不同步

**证据类型：[DIRECT]**

`.env.example` 缺少 `RAG_TIMEOUT_RERANK`，已有专门同步测试稳定失败。

### P2：核心文件过大

**证据类型：[DIRECT]**

| 文件 | 行数 |
|---|---:|
| `agent/loop.py` | 1,519 |
| `api/backup.py` | 970 |
| `api/settings.py` | 844 |
| `agent/tools.py` | 647 |
| `agent/verifier.py` | 621 |
| `rag/pipeline.py` | 556 |

`agent/loop.py` 已同时负责 Agent 编排、SSE、来源聚合、验证、修复、缓存和多种降级路径。继续增加功能会提高回归概率，建议按 generation/retrieval/verification/cache/stream orchestration 拆分。

### P2：测试告警数量过高

**证据类型：[DIRECT]**

最终回归产生 619 条 warning。虽然没有直接阻断，但大量 warning 会掩盖真正的新告警。建议按来源分桶后逐步设为 error，至少对新增 warning 零容忍。

### P2：本地 Python 环境不可复现

**证据类型：[DIRECT]**

项目 `.venv` 的 base executable 已不存在，且 site-packages 中存在 invalid distribution 残留。应重建虚拟环境并提供稳定的 Python 3.12 建立方式，不要依赖被删除的项目内 runtime。

## 9. 架构与实现评价

### 优点

1. **RAG 链路不是简单拼装。** 检索、融合、去重、质量过滤、重排、引用验证、修复和缓存均有独立实现与测试。
2. **Grounding 工程化程度高。** verifier、atomic-unit stream verification、deterministic repair 和 bounded LLM repair 形成完整闭环。
3. **测试资产强。** 754 项收集规模、67% 生产源码覆盖率、多个恢复/故障注入/质量门禁测试，远高于普通个人项目。
4. **前端当前状态稳定。** lint、构建、60 项测试全绿。
5. **可靠性意识较强。** ingestion generation、恢复、超时、缓存失效、备份 revision gate 等均进入了代码，而非仅设计。
6. **安全基础已经存在。** JWT、角色、审计、日志脱敏和备份安全测试均有实现。

### 不足

1. 多个新阶段同时叠加在未封板工作区，导致 schema、启动路径、配置同步和 release artifact 选择发生交叉回归。
2. 核心编排文件过大，单元测试数量虽多，跨模块状态/顺序问题仍能漏出。
3. CI 覆盖率口径包含测试代码，容易高估生产源码覆盖率。
4. 默认 release gate 报告路径没有与最新 canonical artifact 同步。
5. 可选模型路径（OCR、query rewrite）覆盖率接近 0，真实模型能力缺少本次直接证据。

## 10. 发布判定

### 当前判定：No-Go

阻断原因：

1. fresh-start 启动 ImportError；
2. BM25 rebuild schema 破坏；
3. 后端离线套件仍有 2 个失败；
4. Ruff/Mypy 不通过；
5. 当前在线 RAG provenance 已失效；
6. Docker E2E 本次未验证。

### 达到 Go 所需的最小条件

1. 修复 `generate_admin_token` 并新增无 `.env` fresh-start 测试；
2. 修复 dialect/BM25 rebuild 冲突，并新增 rebuild 后真实关键词检索回归；
3. 补齐 `RAG_TIMEOUT_RERANK` 配置样例；
4. Ruff 0、Mypy 0、离线测试 0 failed；
5. 将覆盖率改为生产源码口径，保持不低于 60%；
6. 重新运行当前版本 93×2 在线评测，release gate 0 blocker；
7. 默认 release artifact 与发布工作流绑定；
8. Docker/Compose E2E 全绿；
9. 移除固定 `admin123` bootstrap 策略。

## 11. 推荐执行顺序

### 第一批：立即修复

1. fresh-start token 生成函数；
2. BM25 rebuild schema；
3. `.env.example` 同步；
4. Mypy 4 项和 Ruff 70 项；
5. 为以上问题各补一条能在干净环境复现的测试。

### 第二批：重新封板

1. 重建 `.venv`；
2. 离线全量测试、生产源码覆盖率、前端三门禁；
3. Docker/Compose E2E；
4. 当前版本完整在线 RAG 评测；
5. 固化唯一 release artifact 路径和指纹。

### 第三批：降低后续回归概率

1. 拆分 `agent/loop.py`、`api/backup.py`、`api/settings.py`；
2. 清理 619 条 warning；
3. 补 `settings`、memory、OCR、query rewrite 测试；
4. CI 拆成快速单元门禁和重型集成/在线门禁；
5. 每次评测报告同时记录 Git HEAD、dirty diff hash、dataset/verifier/evaluator/prompt hash 和精确命令。

## 12. 本次执行命令与复现说明

### 后端最终离线回归

```powershell
$env:TEMP='<workspace>/.eval-tmp'
$env:TMP='<workspace>/.eval-tmp'
$env:TIKTOKEN_CACHE_DIR='<existing-cl100k-cache>'
$env:LLM_API_KEY='your-api-key-here'
$env:EMBEDDING_API_KEY='your-api-key-here'
$env:EMBEDDING_DIM='1536'
python -m pytest tests -q -m "not docker" `
  --basetemp '<workspace>/.eval-tmp/pytest-base-final' `
  --disable-warnings --tb=short
```

### 覆盖率

```powershell
python -m pytest tests -q -m "not docker" `
  --cov=. --cov-report=term:skip-covered --cov-fail-under=60
python -m coverage report --omit='tests/*' --skip-covered
```

### 静态检查

```powershell
python -m ruff check backend --config pyproject.toml
python -m mypy backend --config-file pyproject.toml
git diff --check
```

### 前端

```powershell
npm run lint
npm run build
npm test
npm ls --depth=0
```

### 发布门禁

```powershell
cd backend
python release_gate.py
python release_gate.py `
  --report tests/grounded_answer_eval_final_full_rescored.json
```

## 13. 后续报告防混淆模板

未来任何“项目已过门禁”的结论，至少必须同时给出：

```text
evaluation_timestamp:
git_head:
worktree_dirty: true/false
worktree_diff_sha256:
dataset_sha256:
verifier_sha256:
evaluator_sha256:
optimized_prompt_sha256:
report_path:
records_control:
records_optimized:
quality_gate_passed:
performance_gate_passed:
release_gate_command:
release_gate_exit_code:
```

只要其中任一指纹变化，旧报告应标记为 `historical-pass / current-provenance-stale`，不得表述为当前版本已通过。

