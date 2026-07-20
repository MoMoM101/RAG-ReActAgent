# RAG Agent 8.8 → 9.2 最终优化与发布验收方案

> 制定日期：2026-07-11  
> 评测版本：`master@552964f`  
> 当前评分：**8.8/10**  
> 目标评分：**9.1～9.2/10**  
> 核心目标：不继续扩张功能范围，集中消除评测可信度、恢复原子性、部署实证和发布门禁方面的剩余风险。

---

## 1. 当前复测基线

| 检查项 | 实测结果 | 状态 |
|---|---:|:---:|
| 后端全量测试 | 417 passed / 6 skipped / 0 failed | ✅ |
| Ruff | All checks passed | ✅ |
| Mypy | 134 个源码文件无问题 | ✅ |
| 恢复集成测试 | 20 项通过 | ✅ |
| 前端测试 | 41/41 通过 | ✅ |
| 前端 lint | 通过，零 warning | ✅ |
| 前端构建 | 成功，但 LightningCSS 有警告 | ⚠️ |
| Docker Compose 配置 | 校验通过 | ✅ |
| Docker Compose 实际运行 | 尚未完成本地实跑 | ⏸️ |
| Git 交付 | Phase 1/2 已合并 `master` | ✅ |
| Qrels v2 | 代码入口已迁移，正式结果未重新生成 | ⚠️ |

### 当前最重要的事实

1. 后端标准运行方式已经全绿，认证 E2E、恢复测试和前端认证测试均已补齐。
2. `evaluation_results.json` 仍是旧算法结果，其中 Recall 大于 1，不能作为正式质量结论。
3. uploads 恢复仍包含复制到正式目录的窗口，尚未达到进程崩溃级原子性。
4. 恢复一致性还需要覆盖 BM25、文件哈希、chunk ID 集合和模型配置兼容性。
5. Docker CI 已有基础框架，但仍需要真实运行证据和更严格断言。

---

## 2. 发布原则

- OCR、Rerank 始终是可选能力，不得成为服务启动前置条件。
- 任何评测比例指标必须位于 `[0, 1]`，否则报告直接判定无效。
- 恢复成功必须意味着 SQLite、Qdrant、BM25 和 uploads 同时一致。
- 恢复失败必须保持旧系统完整可用，不能只保证数据库可回滚。
- 只有 `master` 上的测试结果可以作为发布证据。
- warning 不视为无关信息；发布候选版本要求项目自身 warning 清零。
- 每个阶段独立提交、独立验证，Gate 未通过不得进入下一阶段。

---

## 3. 总体执行流程

```text
Phase 0  冻结 8.8 分基线
   ↓ Gate 0：提交、测试、配置和结果可追溯
Phase 1  重新生成 Qrels v2 正式基线
   ↓ Gate 1：指标合法、报告可复现、README 一致
Phase 2  完成 uploads 原子切换
   ↓ Gate 2：进程崩溃不会暴露半成品目录
Phase 3  补齐四存储一致性与兼容性策略
   ↓ Gate 3：SQLite/Qdrant/BM25/uploads 集合级一致
Phase 4  Qdrant 临时资源生命周期治理
   ↓ Gate 4：失败无泄漏、成功有保留与清理策略
Phase 5  修复测试路径和前端构建警告
   ↓ Gate 5：任意标准入口稳定、零 warning
Phase 6  Docker 真实端到端冒烟
   ↓ Gate 6：容器链路与可选模型降级通过
Phase 7  master 发布验收
   ↓ Gate 7：形成 9.1～9.2 发布候选版本
```

---

## 4. Phase 0：冻结当前基线

### 工作项

1. 记录当前 commit：`552964f`。
2. 保存以下命令的输出：

```bash
cd backend
pytest tests -q
ruff check . --config ../pyproject.toml
python -m mypy . --config-file ../pyproject.toml

cd ../frontend
npm test
npm run lint
npm run build
```

3. 记录当前模型配置：
   - embedding provider/model/dimension；
   - chunk_size/chunk_overlap；
   - OCR 开关与模型；
   - Rerank 开关与模型；
   - Qdrant collection 名称；
   - qrels schema/version。
4. 检查 `.env`、管理员令牌、数据库、上传文件、模型权重没有进入 Git。

### Gate 0

- [ ] `master` 没有已跟踪业务代码脏改动。
- [ ] 417 个后端测试通过，6 个 skip 原因可解释。
- [ ] 41 个前端测试通过。
- [ ] 当前测试和模型配置已归档。
- [ ] OCR/Rerank 缺失时最小启动仍然通过。

---

## 5. Phase 1：Qrels v2 正式基线

这是当前最高优先级。代码迁移完成并不代表评测迁移完成。

### 5.1 隔离旧结果

当前 `backend/tests/evaluation_results.json` 中存在 Recall 大于 1，应执行：

- 将其移动到明确的 `legacy/` 目录，或重命名为 `evaluation_results_legacy.json`；
- 文件头增加 `metric_version: legacy`；
- README 不再引用该文件作为当前结果；
- CI 禁止把 legacy 文件解析为发布基线。

### 5.2 生成 v2 结果

使用正式 `compute_metrics_v2()` 重新运行评测，并至少记录：

```json
{
  "metric_version": "qrels-v2",
  "qrels_version": "2.0",
  "git_commit": "...",
  "corpus_hash": "...",
  "embedding_provider": "...",
  "embedding_model": "...",
  "embedding_dim": 0,
  "chunk_size": 0,
  "chunk_overlap": 0,
  "ocr_enabled": false,
  "rerank_enabled": false,
  "random_seed": 0
}
```

输出指标至少包括：

- Precision@3/5/10；
- Recall@3/5/10；
- MRR；
- NDCG@3/5/10；
- Hit Rate@3/5/10；
- P50/P95 检索延迟；
- 请求失败率；
- 答案事实命中率；
- 引用正确率；
- 应拒答问题的拒答准确率。

### 5.3 四种模型组合

| 组合 | OCR | Rerank | 目的 |
|---|:---:|:---:|---|
| 最小部署 | Off | Off | 验证无可选模型时仍然可用 |
| OCR 模式 | On | Off | 衡量扫描文档增益 |
| Rerank 模式 | Off | On | 衡量排序质量与延迟成本 |
| 完整模式 | On | On | 建立生产质量上限 |

不能因为本地没有 OCR/Rerank 模型而阻塞整个评测。无法运行的组合应明确记录为 `not_executed`，不能复用其他模式结果冒充。

### 5.4 指标有效性门禁

新增自动化断言：

```python
for metric in ratio_metrics:
    assert 0.0 <= metric <= 1.0
```

还应验证：

- 同一配置重复执行结果差异在合理容差内；
- Recall@K 随 K 增大不能下降；
- Hit Rate@K 随 K 增大不能下降；
- qrels 中不存在无法映射的 document_key；
- 每条结果都能追溯到语料和配置。

### Gate 1

- [ ] 旧结果被明确标记为 legacy。
- [ ] 新结果包含 `metric_version=qrels-v2`。
- [ ] 所有比例指标位于 `[0,1]`。
- [ ] README 展示的数字与新结果文件一致。
- [ ] 最小部署和完整部署结果分开记录。
- [ ] 新报告可通过单条命令重复生成。

---

## 6. Phase 2：uploads 真正原子切换

### 当前问题

当前恢复流程大致为：

```text
live uploads → rename 为 .old
staging uploads → copytree 到 live 路径
删除 .old
```

`copytree` 过程中如果进程被强制终止或机器断电，live 路径可能只包含部分文件。普通异常回滚测试无法覆盖这种崩溃窗口。

### 目标流程

```text
1. 在 uploads 同一父目录创建 uploads.candidate.<restore_id>
2. 完整复制/解压到 candidate
3. 校验文件数量、大小和 SHA-256
4. live uploads → rename 为 uploads.previous.<restore_id>
5. candidate → rename 为 live uploads
6. 完成 SQLite/Qdrant/BM25 提交
7. 延迟清理 previous
```

候选目录必须位于同一文件系统，以保证重命名可以作为原子目录项操作。跨盘时应拒绝原子恢复，或在恢复开始前将 candidate 转移到同盘。

### 测试要求

- candidate 构建失败，live 不变化；
- candidate 哈希失败，live 不变化；
- live 改名失败，candidate 清理；
- candidate 改名失败，previous 恢复为 live；
- 数据提交失败，uploads 回滚；
- 模拟进程重启后，启动恢复器能识别 candidate/previous 残留；
- Windows 文件占用导致 rename 失败时返回可诊断错误。

### Gate 2

- [ ] 正式 live 路径上不再执行长时间 `copytree`。
- [ ] candidate 在切换前完成全部哈希校验。
- [ ] 切换只使用同盘 rename/replace。
- [ ] 启动时能处理上一次中断留下的 candidate/previous。
- [ ] 故障注入测试覆盖每个目录切换点。

---

## 7. Phase 3：四存储一致性与备份兼容性

### 7.1 统一恢复清单

恢复提交前生成统一索引：

```text
document_id
  ├─ sqlite document row
  ├─ expected chunk_ids
  ├─ qdrant point chunk_ids
  ├─ bm25 chunk_ids
  └─ upload path + size + sha256
```

不能只比较总数。总数相等而 ID 不同，必须判定为不一致。

### 7.2 必检内容

| 存储 | 检查内容 |
|---|---|
| SQLite | document ID、状态、chunk_count、raw_text、file_hash |
| Qdrant | collection、向量维度、document ID、chunk ID、point 数量 |
| BM25 | document ID、chunk ID、文本记录数、schema version |
| uploads | 相对路径、大小、SHA-256，不允许越界路径 |

### 7.3 manifest 兼容性信息

manifest 增加：

- backup/schema version；
- splitter version；
- chunk_size/chunk_overlap；
- embedding provider/model/dimension；
- BM25 schema/tokenizer version；
- OCR parser/version；
- 项目 commit/version；
- 每个文件的 size 和 SHA-256。

### 7.4 不兼容策略

| 情况 | 行为 |
|---|---|
| schema 可迁移 | 在 staging 中迁移后再校验 |
| embedding 维度不同 | 拒绝直接切换，允许显式全量重建 |
| chunk 配置不同 | 禁止静默按当前配置重切分 |
| BM25 schema 不兼容 | staging 中重建并重新比对 ID |
| 缺少上传文件 | 默认拒绝恢复 |
| 哈希不一致 | 立即拒绝，保留旧系统 |

### Gate 3

- [ ] SQLite/Qdrant/BM25/uploads 的 document ID 集合一致。
- [ ] 三个索引层的 chunk ID 集合一致。
- [ ] uploads 文件哈希全部通过。
- [ ] embedding 和 chunk 配置兼容性有明确处理策略。
- [ ] 不兼容备份不会静默恢复。

---

## 8. Phase 4：Qdrant collection 生命周期

### 工作项

1. 所有恢复临时 collection 使用统一前缀和 restore ID。
2. 失败时显式删除本轮新建 collection。
3. 成功后暂时保留上一个 active collection，用于紧急回滚。
4. 设置保留规则，例如：
   - 保留当前 active；
   - 保留最近 1 个 previous；
   - 删除超过 24 小时的 restore temp；
   - 删除超过保留数量的历史 collection。
5. 清理失败不能改变恢复成功状态，但必须记录结构化 warning 和指标。
6. 启动时执行一次孤儿 collection 审计，不应盲目删除名称不匹配的集合。

### 测试要求

- 构建失败后临时 collection 被删除；
- 指针切换失败后临时 collection 被删除；
- active 和 previous 不被误删；
- 超期临时集合按策略删除；
- Qdrant 不可用时清理任务安全失败；
- 并发清理与恢复由维护锁互斥。

### Gate 4

- [ ] 恢复失败不遗留临时集合。
- [ ] 恢复成功后旧集合有清晰保留期限。
- [ ] 清理操作受维护锁保护。
- [ ] 有 collection 数量和清理失败指标。

---

## 9. Phase 5：测试稳定性与前端零警告

### 9.1 修复测试路径依赖

`backend/tests/api/test_auth.py` 当前使用：

```python
open("tests/fixtures/sample.txt", "rb")
```

改为基于当前测试文件定位：

```python
FIXTURES_DIR = Path(__file__).resolve().parents[1] / "fixtures"

with (FIXTURES_DIR / "sample.txt").open("rb") as f:
    ...
```

随后分别从仓库根目录和 `backend` 目录运行测试，结果必须一致。

### 9.2 清理前端构建警告

当前 LightningCSS 报告未知规则：

- `@theme`
- `@tailwind`

需要检查：

- Tailwind 主版本与 Vite 插件是否匹配；
- 是否同时混用了 Tailwind v3/v4 配置方式；
- LightningCSS 是否应该处理 Tailwind 编译前语法；
- `index.css` 是否包含已编译产物或重复框架 CSS。

不要简单屏蔽 warning；应确保生成 CSS 中不存在未被浏览器识别的残留规则。

### 9.3 CI 门禁增强

在 `frontend-quality` 中增加：

```yaml
- name: Lint
  run: npm run lint
```

同时将构建 warning 纳入 CI 检查。后端 Docker smoke 的无认证断言从：

```bash
grep -qE "401|200"
```

收紧为必须返回 `401`。

### Gate 5

- [ ] 从仓库根目录和 backend 目录运行测试均全绿。
- [ ] 前端 test/lint/build 全绿。
- [ ] 前端构建无项目 warning。
- [ ] CI 明确执行 frontend lint。
- [ ] 所有无令牌管理接口严格断言 401。

---

## 10. Phase 6：Docker 真实冒烟测试

### 测试矩阵

| 场景 | OCR | Rerank | 必须验证 |
|---|:---:|:---:|---|
| 最小镜像 | Off | Off | 启动、健康、认证、上传、检索、SSE |
| 完整镜像 | On | On | 模型加载、健康状态、OCR、Rerank |
| OCR 故障 | Error | Off/On | 自动降级，基础问答可用 |
| Rerank 故障 | Off/On | Error | 使用原始排序返回结果 |

### 端到端步骤

1. `docker compose up -d --build`。
2. 等待 backend/frontend 健康检查。
3. 通过 frontend proxy 请求 `/api/health`。
4. 验证无令牌访问管理接口返回 401。
5. 使用正确令牌创建会话。
6. 上传小型 TXT/PDF 测试文件。
7. 等待处理完成并执行检索。
8. 完成一次 SSE 问答并检查引用。
9. 重启容器，验证持久化数据仍存在。
10. 执行一次备份与测试环境恢复。
11. 检查只有设计中的端口暴露到宿主机。
12. 保存容器日志和最终健康状态。

### CI 建议

- compose smoke 超时后输出 `docker compose ps` 和容器日志；
- 健康循环结束后显式判断服务是否成功，不能只依赖循环退出；
- 使用唯一测试令牌，不在日志中输出令牌；
- 即使测试失败也执行 `docker compose down -v`；
- 对上传、会话和 SSE 返回内容做断言，而不仅检查 HTTP 连接成功。

### Gate 6

- [ ] 最小镜像完整链路通过。
- [ ] OCR/Rerank 未部署时服务可运行。
- [ ] 模型加载失败时健康接口明确报告 degraded。
- [ ] 前端代理、认证、上传、检索和 SSE 全部通过。
- [ ] 容器重启后数据持久化正常。
- [ ] CI 可重复完成 compose smoke。

---

## 11. Phase 7：发布候选验收

### master 完整门禁

```bash
git status --short
git diff --check

cd backend
pytest tests -q
ruff check . --config ../pyproject.toml
python -m mypy . --config-file ../pyproject.toml

cd ../frontend
npm test
npm run lint
npm run build

cd ..
docker compose config --quiet
docker compose up -d --build
```

### 发布定义

- [ ] 后端测试零失败，skip 原因全部可解释。
- [ ] Ruff、Mypy 全绿。
- [ ] 前端测试、lint、build 零失败、零 warning。
- [ ] Qrels v2 新结果有效且 README 一致。
- [ ] 最小模式和完整模式分别有评测记录。
- [ ] 恢复四存储一致性与故障注入测试通过。
- [ ] uploads 切换不暴露半成品目录。
- [ ] Qdrant 临时集合可回收。
- [ ] Docker 真实端到端冒烟通过。
- [ ] `.env`、令牌、数据、上传文件和模型权重未进入 Git。
- [ ] `master` 工作区干净并已创建发布 tag。

---

## 12. 推荐提交拆分

建议按以下主题提交，避免形成难以回滚的大提交：

```text
test: make auth upload fixture independent of cwd
eval: regenerate canonical qrels v2 baselines
docs: publish validated qrels v2 metrics
refactor: atomically switch restored upload directories
feat: validate bm25 and cross-store restore consistency
feat: manage qdrant restore collection lifecycle
fix: remove tailwind lightningcss build warnings
ci: enforce frontend lint and strict auth smoke assertions
test: add full compose end-to-end smoke coverage
```

每个提交后至少执行与变更直接相关的测试；Phase 完成后再执行全量门禁。

---

## 13. 优先级与工作量

| 优先级 | 工作项 | 预计工作量 | 评分收益 |
|:---:|---|:---:|:---:|
| P0 | 重新生成并发布 Qrels v2 正式基线 | 0.5～1 天 | +0.10～0.15 |
| P0 | uploads 同盘原子切换 | 0.5～1 天 | +0.10 |
| P0 | BM25/哈希/chunk ID 一致性 | 1 天 | +0.05～0.10 |
| P1 | Qdrant 临时集合生命周期 | 0.5 天 | +0.05 |
| P1 | Docker 完整端到端冒烟 | 0.5～1 天 | +0.05～0.10 |
| P1 | 前端构建 warning 清零 | 0.5 天 | +0.03 |
| P2 | 测试路径与 CI 严格断言 | 0.25 天 | +0.02 |

总计预计：**3.5～5 个开发日**。

---

## 14. 目标评分路径

| 完成范围 | 预期评分 |
|---|:---:|
| 当前版本 | 8.8 |
| Qrels v2 正式报告 + 测试路径修复 | 8.9 |
| uploads 原子切换 + 四存储一致性 | 9.0～9.1 |
| Qdrant 生命周期 + Docker 实跑 | 9.1 |
| master 零失败、零 warning、完整发布证据 | **9.2** |

最终 9.2 分的判断依据不是功能数量，而是以下五项都具备自动化证据：

1. **质量可信**：Qrels v2 指标合法且可复现；
2. **数据安全**：恢复过程中任何失败都不破坏旧数据；
3. **部署可信**：Docker 中真实完成认证、上传、检索和 SSE；
4. **降级可靠**：OCR/Rerank 缺失或故障时基础功能仍然可用；
5. **持续可维护**：主分支 CI 零失败、零 warning，结果可追溯。
