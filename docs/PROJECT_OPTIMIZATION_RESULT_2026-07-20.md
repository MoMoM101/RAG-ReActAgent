# RAG Agent 第一轮优化结果

> 后续：第二轮维护性、覆盖率、warning 与前端加载优化见 `PROJECT_OPTIMIZATION_PHASE2_RESULT_2026-07-20.md`。

> 日期：2026-07-20  
> 设计：`PROJECT_OPTIMIZATION_DESIGN_2026-07-20.md`  
> 基线评测：`PROJECT_EVALUATION_2026-07-20.md`  
> 范围：Phase 1 发布阻断修复

## 结果摘要

本轮已完成 Phase 1 的代码和门禁优化。此前稳定复现的两个后端测试失败已经消除，fresh-start 启动 ImportError 已修复，Ruff/Mypy 均恢复为零问题，前后端离线门禁全绿。

当前仍不能宣布正式发布通过，因为最新在线 RAG 报告与当前 dataset/verifier/evaluator 存在 3 个 provenance 指纹差异，且本轮没有运行 Docker daemon E2E 或重新生成 93×2 在线评测。

## 完成项

### Fresh-start 安全令牌

- 新增 `security.generate_admin_token()`，使用 `secrets.token_urlsafe(32)`；
- `_bootstrap_admin_token()` 不再导入不存在的函数；
- 新增随机性、URL-safe、隔离 env 持久化和不覆盖已有 token 测试；
- 测试不读写真实 `.env`。

### BM25/FTS schema

- `SqliteAdapter.rebuild_fts()` 不再把 `bm25_docs` 创建为 FTS5 virtual table；
- 重建后恢复当前 BM25 所需的 `bm25_docs`、`bm25_index`、`bm25_stats` 普通关系表；
- legacy `chunks_fts` 使用独立 FTS5 表；
- 新增 schema 类型和重建后真实关键词检索回归；
- 原先的跨测试顺序污染已消失。

### 配置、类型与静态检查

- `.env.example` 新增 `RAG_TIMEOUT_RERANK=5.0`；
- `User.role` 更新路径显式转换为 `UserRole`；
- `ScriptDirectory` 使用 `TYPE_CHECKING` + postponed annotations 正确标注；
- 清理未使用导入、导入顺序、异常链、旧类型写法、超长行等问题；
- 未降低 Ruff/Mypy 规则，没有使用批量 `noqa`。

### Canonical release report

- `release_gate.py` 默认报告改为 `grounded_answer_eval_final_full_rescored.json`；
- release workflow 显式传入同一路径；
- 新增默认报告路径回归测试；
- provenance、质量和性能门槛保持不变。

## 优化前后对比

| 门禁 | 优化前 | 优化后 |
|---|---:|---:|
| 后端离线通过 | 725 | 731 |
| 后端离线失败 | 2 | **0** |
| 后端跳过 | 18 | 18 |
| Docker 用例排除 | 9 | 9 |
| Ruff | 70 个问题 | **0** |
| Mypy | 4 个错误 | **0** |
| 前端测试 | 60/60 | **60/60** |
| 前端 lint/build | 通过 | **通过** |
| `git diff --check` | 2 个问题 | **通过** |
| release gate 默认报告 | 旧 v4 报告，15 blockers | canonical 报告，仅 3 个 provenance blockers |

## 最终验证数据

### 后端

```text
collected 758 items / 9 deselected / 749 selected
731 passed, 18 skipped, 9 deselected, 0 failed
179.39 seconds
```

### 静态检查

```text
Ruff: All checks passed!
Mypy: Success: no issues found in 194 source files
```

### 前端

```text
Oxlint: passed
TypeScript + Vite build: passed
Vitest: 8 files, 60 tests passed
```

### Release gate

默认命令现在读取 canonical rescored full report。当前只剩：

- stale `dataset_sha256`；
- stale `verifier_sha256`；
- stale `evaluator_sha256`。

没有质量或性能指标 blocker。这保持了“历史版本确实过门禁”和“当前版本需要重新生成 provenance”之间的正确边界。

## 剩余工作

### 发布前必须完成

1. 运行 Docker/Compose E2E；
2. 对当前工作区重新生成 93×2 在线 RAG 评测；
3. 确认 canonical report 的 provenance、质量和性能全部通过；
4. 处理固定 `admin/admin123` bootstrap 策略，需要先确定首次部署凭据交付方式。

### 后续优化

1. 拆分 `agent/loop.py`、`api/backup.py`、`api/settings.py`；
2. 清理测试 warning；
3. 提高 settings、memory、OCR、query rewrite 覆盖率；
4. 重建项目 `.venv`，消除缺失 base runtime；
5. 将 CI 覆盖率改为只统计生产源码。

## 当前判定

- 功能与离线工程门禁：**通过**；
- 当前在线 RAG 发布证据：**待重新生成**；
- Docker E2E：**本轮未验证**；
- 正式发布：**No-Go，剩余阻断已从代码正确性转为在线 provenance、Docker 验收和首次管理员凭据策略。**
