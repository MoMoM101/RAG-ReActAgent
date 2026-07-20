# RAG Agent 第二轮优化与实测结果

> 日期：2026-07-20（Asia/Shanghai）  
> 基线：`PROJECT_OPTIMIZATION_RESULT_2026-07-20.md`  
> 明确排除：固定管理员密码策略，本轮按要求不修改

> 后续：镜像瘦身、表格加载器去 pandas 与仓库级 Ruff 收口见 `PROJECT_OPTIMIZATION_PHASE3_RESULT_2026-07-20.md`。

## 结论

第二轮工程优化已完成并通过本地全量回归与真实 Docker 端到端验收。最终 Docker Run ID 为 `ragagent-e2e-20260720-023509-3f559250`，12 个阶段全部通过；后端非 Docker 测试为 738 passed / 18 skipped / 9 deselected；前端 60/60 测试通过。

需要特别区分两件事：

- RAG 历史完整评测的质量指标已经超过门禁，本轮没有发现新的质量或性能 blocker；
- 当前 canonical release report 对 dataset、verifier、evaluator 的 3 个 provenance 哈希已过期，因此在“当前代码版本的可追溯发布证据”口径下仍是 No-Go。这里不是 RAG 质量不达标，而是必须重新生成当前版本的 93×2 在线评测证据。

## 本轮完成的优化

### 1. Agent 来源处理模块化

- 新增 `backend/agent/source_utils.py`；
- 从 `agent/loop.py` 移出来源解析、上下文裁剪、Jaccard 去重、相邻块合并和提示注入检测；
- `agent/loop.py` 从约 1644 行降至约 1460 行；
- 新增 4 个来源处理回归测试；
- 不修改提示词、qrels、评分器或门禁阈值。

### 2. 覆盖率与 warning 门禁

- 新增 `backend/.coveragerc`，仅统计生产源码并启用 branch coverage；
- CI 排除 Docker marker，生成终端与 XML 覆盖率报告；
- `--cov-fail-under=60` 保持不变；
- Alembic 使用 `path_separator = os`；
- 修复 Pytest 10 class fixture 弃用写法；
- RuntimeWarning、DeprecationWarning、PytestRemovedIn10Warning 升级为错误；
- 仅对故障注入中预期的 Qdrant 不可用 warning 做精确过滤。

覆盖率实测快照：

```text
735 passed, 18 skipped, 9 deselected
production source branch coverage: 63.14%
coverage gate: passed (minimum 60%)
```

该快照早于本轮最后新增的 3 个测试，因此不虚构更新后的覆盖率百分比；最终普通全量回归计数见下文。

### 3. 前端首屏拆包

- Chat 首页保持同步加载；
- Documents、Settings、Memories 使用 `React.lazy` 按路由加载；
- 增加可访问的 `Suspense` loading 状态；
- React 与 Markdown 生态拆成稳定 vendor chunks；
- 没有增加运行时依赖。

| 指标 | 优化前 | 优化后首屏 | 变化 |
|---|---:|---:|---:|
| 原始 JS | 454.83 kB | 417.74 kB | -8.2% |
| Gzip JS | 140.50 kB | 131.54 kB | -6.4% |
| 延迟加载业务页面 | 0 | 3 | +3 |

延迟页面产物分别约为 Memory 4.19 kB、Settings 17.11 kB、Documents 17.22 kB。

### 4. Docker 首次启动与验收可靠性

真实 Compose 验收过程中发现并修复了以下问题：

- 后端镜像启动前执行 `alembic upgrade head`，新数据卷可直接启动；
- 验收脚本兼容 Windows PowerShell 5.1；
- 使用 Compose `--env-file`，不复制、不挂载、不输出项目 `.env`；
- 原生命令 stderr 不再被 PowerShell 误判为脚本失败；
- 端口冲突只检查 `Listen`，不再把 `TIME_WAIT`/PID 0 判为占用；
- pytest 9 的 `<testsuites><testsuite>` JUnit 结构可正确解析；
- 一致性检查改在 backend 容器内执行，避免宿主机误连本地 SQLite/Qdrant；
- Qdrant collection 首次并发创建改为 race-safe；
- Qdrant 500、replica unavailable 等瞬态 upsert 失败执行 3 次幂等退避重试；
- 恢复后 SSE 输出按完整文本校验并保留日志。

### 5. 测试环境稳定性

- 修复项目 `.venv` 启动器，当前为 Python 3.12.13 / pytest 9.1.1；
- Embedding mock 维度由硬编码 1536 改为跟随当前配置，避免 1024/1536 集合互相污染；
- 需要真实 Embedding 密钥的 3 个集成测试在无有效密钥时明确 skip；
- 新增 Qdrant 创建竞态、瞬态 upsert 重试和 Docker migration 启动顺序回归测试。

## Docker 真实端到端数据

正式产物：`artifacts/docker-e2e/ragagent-e2e-20260720-023509-3f559250/`

```text
overall: passed
stages: 12/12 passed
wall time: 78.5 s
uploaded documents: 2/2 ready
live consistency: 2 documents / 2 chunks
SSE QA: 2/2 passed
faithfulness: 1.0 / 1.0
citation precision: 1.0 / 1.0
citation recall: 1.0 / 1.0
restart persistence: 2/2 documents retained
backup size: 11,257 bytes
backup restore: 2/2 documents restored and ready
degradation: qdrant=error, sqlite=ok
recovery: health=ok, qdrant=ok
Docker smoke: 5/5 passed
backend image size: 1.09 GB
```

全部通过阶段：config_check、build、health、secrets_check、auth_check、upload、consistency、sse_qa、restart_persistence、backup_restore、degradation、smoke。通过后测试容器、网络和数据卷已自动清理，正式报告与日志保留。

## RAG 当前实测口径

93 条数据集的离线 retrieval dry-run：

```text
queries: 93
answerable: 66
unanswerable: 27
answerable retrieval hit@k: 100%
all relevant-query retrieval hit@k: 92.405%
```

Release Gate 当前仅报告 3 个 stale provenance blocker：dataset、verifier、evaluator。没有新的质量或性能 blocker。完整 93×2 在线生成评测尚未在本轮代码上重跑，因为它涉及 186 次模型生成调用和额外费用；不能用历史报告伪装成当前 provenance。

## 最终验证清单

```text
Backend full: 738 passed, 18 skipped, 9 deselected, 184.03 s
Backend targeted Qdrant: 6/6 passed
Production branch coverage snapshot: 63.14%, gate passed
Mypy: Success, no issues in 198 source files
Ruff (本轮修改文件): All checks passed
Frontend lint: passed
Frontend build: passed
Frontend tests: 8 files, 60/60 passed
Docker E2E: 12/12 stages passed
Docker smoke: 5/5 passed
RAG retrieval dry-run: answerable hit@k 100%
```

仓库级 Ruff 仍会报告 29 个 import 排序问题，主要位于本轮开始前已修改的旧文件；本轮没有批量格式化这些用户改动。本轮实际修改文件已单独严格检查通过。

## 后续仍值得优化

1. 经费用确认后，执行当前版本完整 93×2 在线评测并刷新 3 个 provenance 哈希；
2. 将 1.09 GB 后端镜像改为依赖分层/多阶段构建，减少运行镜像体积和漏洞面；
3. 继续拆分 `api/settings.py`、`api/backup.py` 和剩余 Agent 编排；
4. 优先补齐 settings、memory、OCR、query rewrite 分支覆盖率；
5. 在不覆盖用户既有改动的前提下，单独整理仓库级 29 个 Ruff import 排序问题；
6. 将 Docker E2E 纳入可访问模型密钥的受保护 CI/nightly 环境。

固定管理员密码优化按用户要求暂缓，不计入本轮缺陷。

## Claude Code 执行说明

本轮按既定协作方式准备了 Claude Code 小范围执行合同，但调用被租户数据外传策略阻止：工作区包含大量未提交私有代码，不能发送给 Anthropic。实现和验证随后均由 Codex 在本地完成，没有通过 Claude Code 发送仓库内容。
