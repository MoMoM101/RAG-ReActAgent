# RAG Agent 优化设计与执行路线

> 日期：2026-07-20  
> 输入基线：`PROJECT_EVALUATION_2026-07-20.md` 的当前工作区实测结果  
> 原则：先恢复正确性与门禁，再改善安全和可维护性，最后重新建立在线质量证据

## 目标

把当前工作区从“能力完整但未封板”推进到可复验、可发布的状态，同时避免大规模重构与正在进行的功能改动互相污染。

本轮不追求新增功能，优先消除会导致启动失败、检索失效、CI 阻断和评测误判的问题。

## 设计原则

1. 正确性优先于性能微调。
2. 每个缺陷必须有能在干净环境复现的回归测试。
3. 历史在线评测与当前版本 provenance 必须明确分离。
4. 不修改 qrels、评测结果 JSON、Agent 提示词或 RAG 质量策略来“制造通过”。
5. 保留现有 API 与数据库迁移兼容性；涉及 schema 的修复必须验证旧表和新表的类型。
6. 当前工作区改动很多，本轮仅做小而闭合的修复，不做 `agent/loop.py` 等核心模块的大重构。

## Phase 1：发布阻断修复（本轮执行）

### 1. Fresh-start 管理令牌

- 在安全模块提供使用 `secrets.token_urlsafe()` 的令牌生成函数；
- `_bootstrap_admin_token()` 不再导入不存在的符号；
- 添加“无已有 token 时能生成并持久化”“已有 token 时不覆盖”的测试；
- 测试必须使用临时 env 文件，不能读写真实 `.env`。

### 2. BM25/FTS schema 冲突

- `SqliteAdapter.rebuild_fts()` 不得把关系型 `bm25_docs` 替换为 FTS5 virtual table；
- 重建后 `bm25_docs`、`bm25_index`、`bm25_stats` 保持当前 BM25 schema；
- legacy `chunks_fts` 如需保留，必须使用独立名称；
- 添加 schema 类型断言和重建后的实际关键词检索测试；
- 保证测试顺序不会污染后续 retrieval 测试。

### 3. 配置与类型门禁

- `.env.example` 补齐 `RAG_TIMEOUT_RERANK`；
- 修复 Mypy 4 个错误；
- 修复 Ruff 全部错误，不采用大范围 `noqa` 或降低规则；
- 不改变既有业务行为来迁就 lint。

### 4. Canonical release artifact

- release workflow 显式传入唯一报告路径；
- `release_gate.py` 的默认路径与 workflow 保持一致；
- 测试确认默认路径不会再次回到旧 `v4_final` 报告；
- 当前报告 provenance 仍可失败，这是正确行为；不得放松 provenance 或质量门槛。

## Phase 2：安全收口（Phase 1 后）

1. 移除固定 `admin/admin123` bootstrap 策略；
2. 采用部署时提供初始密码或一次性随机凭据；
3. 强制首次登录改密；
4. 关闭默认 legacy admin token，提供显式迁移开关；
5. 对公开 metrics/health tasks 做部署暴露审查；
6. 增加依赖漏洞与 secret scan 门禁。

## Phase 3：可维护性优化

按风险从低到高拆分：

1. `api/settings.py`：配置读写、连接测试、重建编排、SSE 进度；
2. `api/backup.py`：归档校验、revision 分类、迁移、原子切换；
3. `agent/loop.py`：turn orchestration、tool execution、generation、verification/repair、cache；
4. 将跨模块状态清理改成显式 fixture/context，减少测试顺序依赖；
5. 清理 warning，并将新增 warning 作为 CI 失败处理。

## Phase 4：质量证据重建

在 Phase 1–3 的必要改动完成后：

1. 后端离线测试 0 failed；
2. Ruff 0、Mypy 0；
3. 生产源码覆盖率不低于 60%；
4. 前端 lint/build/test 全绿；
5. Docker/Compose E2E 全绿；
6. 对当前 dataset/verifier/evaluator/prompt 重新运行 93×2 在线评测；
7. canonical release report 的 provenance、质量、性能门禁全部通过；
8. 报告写入 Git HEAD、dirty diff hash、执行命令和时间戳。

## 本轮验收标准

- fresh-start token 回归测试通过；
- dialect rebuild 后 BM25 表不是 virtual table，关键词检索可用；
- `.env.example` 同步测试通过；
- 之前稳定复现的 2 个离线失败消失；
- Ruff 0；
- Mypy 0；
- release gate 默认/CI 使用同一 canonical 路径，且不放松任何门槛；
- 不修改评测数据、评测结果 JSON、Agent 提示词和前端功能。

