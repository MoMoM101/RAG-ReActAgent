# RAG Agent 9.0 → 9.2 发布闭环优化方案

> 制定日期：2026-07-13  
> 当前版本：`master@24469a8`  
> 当前评分：**9.0/10**  
> 目标评分：**9.2/10**  
> 文档定位：最终发布闭环，不再扩展非必要功能。

---

## 1. 当前已验证基线

| 检查项 | 实测结果 | 状态 |
|---|---:|:---:|
| 后端全量测试 | 419 passed / 4 skipped / 0 failed | ✅ |
| Ruff | All checks passed | ✅ |
| Mypy | 134 个源码文件无问题 | ✅ |
| 前端测试 | 41/41 通过 | ✅ |
| 前端 lint | 零 warning | ✅ |
| 前端构建 | 成功、零 warning | ✅ |
| Docker Compose 配置 | 校验通过 | ✅ |
| Docker 真实端到端运行 | 尚无本轮实跑证据 | ⏸️ |
| Qrels v2 指标 | 数值合法，README 已更新 | ✅ |
| Qrels v2 结果文件 | 已生成，但尚未加入 Git | ❌ |
| uploads 原子切换 | 已实现 candidate/previous 模式 | ✅ |
| BM25 一致性 | 已检查文档分块数量，未比对 chunk ID 集合 | ⚠️ |
| Qdrant 生命周期 | 已实现临时集合清理与保留策略 | ✅ |

### 当前有效的 Qrels v2 指标

| 指标 | 数值 |
|---|:---:|
| Precision@5 | 67.1% |
| Recall@5 | 93.5% |
| MRR | 91.4% |
| NDCG@5 | 93.5% |
| Hit@5 | 93.5% |
| 平均检索延迟 | 469.6ms |

这些比例指标均位于 `[0,1]`，旧版 Recall 大于 1 的计算错误已经消除。

---

## 2. 剩余问题与优先级

| 优先级 | 问题 | 发布影响 |
|:---:|---|---|
| P0 | `evaluation_results_v2.json` 未加入 Git | README 引用不存在于仓库的发布资产 |
| P0 | 恢复新增关键逻辑缺少对应专项测试 | 原子切换、清理和兼容性只能靠代码审查证明 |
| P1 | BM25 只校验数量，不比对 chunk ID 集合 | 数量相同但内容错误时可能漏检 |
| P1 | Rerank/no-rerank 结果完全一致 | 无法确认模型真正生效还是静默降级 |
| P1 | keyword-only 指标为 0 | 可能存在评测数据未写入 BM25 的问题 |
| P1 | 缺少 OCR/Rerank 四模式评测 | 无法量化可选模型实际收益和降级质量 |
| P1 | Docker Compose 未完成真实端到端实跑 | 部署能力尚缺运行证据 |
| P2 | `.claude` 和多份文档未跟踪 | 发布仓库状态不够干净、文档可能重复 |

---

## 3. 总体闭环流程

```text
Phase 0  固化 9.0 基线
   ↓ Gate 0：当前结果可追溯
Phase 1  提交 Qrels v2 正式资产
   ↓ Gate 1：README、JSON、commit 完全一致
Phase 2  为恢复新增逻辑补专项测试
   ↓ Gate 2：原子切换、回滚、清理、兼容性有自动化证据
Phase 3  BM25 升级为 chunk ID 集合校验
   ↓ Gate 3：SQLite/Qdrant/BM25 分块身份一致
Phase 4  诊断 Rerank 与 keyword-only 评测异常
   ↓ Gate 4：组件是否生效可证明
Phase 5  完成 OCR/Rerank 四模式评测
   ↓ Gate 5：完整模式收益和最小模式质量可量化
Phase 6  Docker 真实端到端冒烟
   ↓ Gate 6：容器发布链路可复现
Phase 7  仓库清理与最终验收
   ↓ Gate 7：形成 9.2 发布候选版本
```

---

## 4. Phase 0：固化当前基线

### 工作项

1. 记录当前 commit：`24469a8`。
2. 保存全量测试输出和运行环境：

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

3. 记录当前评测配置：
   - embedding：`qwen/text-embedding-v4`；
   - embedding dimension：1024；
   - chunk_size：200；
   - chunk_overlap：40；
   - reranker：`BAAI/bge-reranker-v2-m3`；
   - qrels version：2.0；
   - 查询数量：31。
4. 记录 4 个 skip 的具体原因；发布说明中不得只写“全部通过”而忽略 skip。

### Gate 0

- [ ] 后端 419 项通过。
- [ ] 前端 41 项通过。
- [ ] Ruff、Mypy、前端 lint/build 全绿。
- [ ] 4 个 skip 均有可解释原因。
- [ ] 当前评测配置和 commit 已归档。

---

## 5. Phase 1：提交 Qrels v2 正式资产

### 当前问题

README 已引用：

```text
backend/tests/evaluation_results_v2.json
```

但该文件当前处于未跟踪状态。其他开发者克隆仓库后无法得到 README 声称的正式基线。

### 工作项

1. 检查结果文件不包含 API Key、绝对用户路径、数据库路径等敏感信息。
2. 验证结果中的：
   - `metric_version == "qrels-v2"`；
   - `qrels_version == "2.0"`；
   - `git_commit` 存在；
   - 所有比例指标位于 `[0,1]`；
   - README 数值与 JSON 一致。
3. 将结果加入 Git：

```bash
git add backend/tests/evaluation_results_v2.json
git commit -m "eval: add validated qrels v2 baseline results"
```

4. 新增 CI 校验脚本，防止 README 与 JSON 再次漂移。

### 建议 CI 断言

```python
assert result["metric_version"] == "qrels-v2"

for value in all_ratio_metrics(result):
    assert 0.0 <= value <= 1.0

assert readme_precision == round(result["aggregate_no_rerank"]["precision"]["5"] * 100, 1)
```

### Gate 1

- [ ] `git ls-files` 能找到 v2 结果文件。
- [ ] README 指标与 JSON 完全一致。
- [ ] JSON 不包含 Secret 和个人绝对路径。
- [ ] CI 能检查指标范围和 README 漂移。

---

## 6. Phase 2：恢复新逻辑专项测试

### 背景

近期恢复流程新增了大量关键代码：

- candidate/previous 原子目录切换；
- candidate SHA-256 校验；
- 启动残留清理；
- BM25 一致性；
- embedding/chunk 配置兼容性；
- Qdrant 临时集合生命周期。

现有 20 个恢复测试仍应保留，但需要针对上述新分支增加直接测试。

### 6.1 uploads 原子切换测试

- candidate 文件数量不一致时拒绝切换；
- candidate 文件大小不一致时拒绝切换；
- candidate SHA-256 不一致时拒绝切换；
- live → previous rename 失败时 live 保持可用；
- candidate → live rename 失败时 previous 恢复成功；
- previous 恢复也失败时记录高优先级故障；
- Windows 文件占用导致 rename 失败时返回可诊断错误；
- candidate 构建阶段允许复制，但 live 路径上不执行长时间 copytree。

### 6.2 启动残留清理测试

- 删除上次中断留下的 `uploads.candidate.*`；
- 删除安全可判定的 `uploads.previous.*`；
- 当前 live uploads 永不被清理；
- 无权限删除时记录 warning，启动不崩溃；
- 不匹配规定前缀的目录不被误删。

### 6.3 兼容性测试

- embedding 维度不同，恢复被拒绝；
- chunk_size 不同，恢复被拒绝或进入显式重建模式；
- chunk_overlap 不同，行为符合 manifest 策略；
- BM25 schema version 不兼容时拒绝恢复；
- 旧版 manifest 按兼容策略处理，不能静默假定兼容。

### 6.4 Qdrant 生命周期测试

- Qdrant 构建失败后临时 collection 被删除；
- 指针切换失败后临时 collection 被删除；
- active collection 不被误删；
- previous collection 按保留策略保留；
- 超期 restore collection 被删除；
- Qdrant 不可用时清理安全失败；
- 清理与恢复操作受维护锁互斥。

### Gate 2

- [ ] 每个新增关键分支至少有一个正向和一个失败测试。
- [ ] 恢复失败后 SQLite、Qdrant、BM25、uploads 均保持旧状态。
- [ ] 临时目录和临时 collection 不泄漏。
- [ ] 测试不依赖真实云模型或外部 Qdrant 服务。

---

## 7. Phase 3：BM25 chunk ID 集合级一致性

### 当前问题

当前主要校验每个 document 在 BM25 中的 chunk 数量：

```text
expected=3, actual=3 → 通过
```

但数量相同不代表分块身份相同：

```text
SQLite expected：A、B、C
BM25 actual：X、Y、Z
```

### 目标设计

为每个分块建立稳定 ID：

```text
chunk_id = UUIDv5(document_id + ":" + chunk_index)
```

恢复提交前比较：

```text
expected_chunk_ids
== qdrant_chunk_ids
== bm25_chunk_ids
```

如果 BM25 表还没有 `chunk_id` 字段，建议：

1. 新增 schema migration；
2. 写入 BM25 时同步保存稳定 chunk ID；
3. 为旧数据提供一次可回滚重建；
4. schema version 更新到新版本；
5. 备份 manifest 记录 BM25 schema version。

### 必测场景

- 三方 ID 完全一致；
- 数量一致但 ID 不一致；
- BM25 缺少一个 chunk；
- Qdrant 多出一个 chunk；
- 同一 chunk ID 重复；
- document ID 正确但 chunk_index 错位；
- 旧 BM25 schema 触发重建或拒绝策略。

### Gate 3

- [ ] 不再只依赖 chunk_count 判断一致性。
- [ ] SQLite/Qdrant/BM25 使用同一稳定 chunk ID 规则。
- [ ] 数量一致但 ID 不一致能够被测试捕获。
- [ ] schema migration 有回滚或重建路径。

---

## 8. Phase 4：诊断评测异常

### 8.1 keyword-only 为 0

当前消融结果中 keyword-only 的 Precision、MRR、Hit Rate、NDCG 全为 0。需要依次验证：

1. 评测语料是否实际写入 BM25 表；
2. 写入的 document_key/section_key 是否与 qrels 一致；
3. 查询 tokenizer 是否适配中文和英文混合文本；
4. 评测结束前 BM25 transaction 是否已提交；
5. keyword retriever 是否指向正确数据库和表；
6. 返回结果是否在转换为 `RetrievedItem` 时丢失稳定 key。

增加最低限度断言：

```python
assert bm25_indexed_document_count == expected_document_count
assert keyword_results_for_known_exact_term
```

### 8.2 Rerank 与 no-rerank 完全一致

需要在结果中记录实际运行状态，而不仅是配置开关：

```json
{
  "rerank_requested": true,
  "rerank_loaded": true,
  "rerank_model": "BAAI/bge-reranker-v2-m3",
  "rerank_fallback_reason": null,
  "queries_with_order_change": 12
}
```

验证：

- 模型是否实际加载；
- 输入候选数量是否大于最终 top_n；
- Rerank 前后顺序是否发生变化；
- 分数是否全部相同或为空；
- 降级时是否被误记为“Rerank 已启用”。

如果模型实际工作但指标不变，应在报告中给出“顺序变化查询数”和延迟成本，说明它对当前小语料没有质量收益。

### Gate 4

- [ ] keyword-only 能命中已知精确关键词。
- [ ] BM25 评测前索引文档数符合预期。
- [ ] 报告区分 requested、loaded、degraded。
- [ ] Rerank 前后顺序变化数量可观测。
- [ ] 静默降级不会被报告为模型已生效。

---

## 9. Phase 5：OCR/Rerank 四模式评测

### 测试矩阵

| 模式 | OCR | Rerank | 主要目的 |
|---|:---:|:---:|---|
| A 最小模式 | Off | Off | 测量兜底质量和最小依赖延迟 |
| B OCR 模式 | On | Off | 测量扫描文档解析收益 |
| C Rerank 模式 | Off | On | 测量排序收益和延迟成本 |
| D 完整模式 | On | On | 测量生产质量上限 |

### 数据集要求

- 普通文本 PDF；
- 扫描 PDF；
- 图片文档；
- TXT/Markdown；
- 表格和多栏文档；
- 中英文混合内容；
- 无答案问题；
- 容易产生错误引用的问题。

### 每种模式必须记录

- Precision/Recall/MRR/NDCG/Hit Rate；
- P50/P95 检索延迟；
- P50/P95 端到端回答延迟；
- OCR 成功率和降级次数；
- Rerank 加载状态和顺序变化数；
- 答案事实命中率；
- 引用正确率；
- 拒答准确率；
- 请求失败率；
- 峰值内存或显存占用。

### Gate 5

- [ ] 四种模式分别生成独立 JSON。
- [ ] 每份结果记录实际组件状态。
- [ ] 最小模式在无 OCR/Rerank 时仍能正常完成测试。
- [ ] 完整模式的收益和资源成本均可量化。
- [ ] README 不混用不同模式的指标。

---

## 10. Phase 6：Docker Compose 真实端到端冒烟

### 执行步骤

```bash
docker compose config --quiet
docker compose up -d --build
docker compose ps
```

随后自动验证：

1. backend 健康检查成功；
2. frontend 能返回 HTML；
3. frontend proxy 能访问 `/api/health`；
4. 无令牌访问管理接口严格返回 401；
5. 错误令牌严格返回 401；
6. 正确令牌能够列出文档；
7. 创建会话；
8. 上传一个小型 TXT 文件；
9. 等待文档处理完成；
10. 执行检索和一次 SSE 问答；
11. 检查回答引用包含上传文档；
12. 重启容器并验证文档仍存在；
13. 执行一次测试备份恢复；
14. 禁用 OCR/Rerank 后重复基础问答；
15. 检查宿主机只暴露设计中的端口。

### CI 加固

- 健康等待循环结束后必须显式判断成功；
- 失败时输出 `docker compose ps` 和服务日志；
- 不在日志中输出管理员令牌；
- 即使测试失败也执行 cleanup；
- 对 JSON/SSE 内容做业务断言，而不只检查 HTTP 200；
- Dockerfile、Compose 或依赖文件变化时必须运行该 job。

### Gate 6

- [ ] 镜像构建成功。
- [ ] 认证、上传、检索、SSE 全链路通过。
- [ ] 容器重启后数据持久化正常。
- [ ] OCR/Rerank 禁用时基础链路仍然通过。
- [ ] Docker 中备份恢复成功。
- [ ] CI 可重复运行并保留失败诊断信息。

---

## 11. Phase 7：仓库清理与发布验收

### 11.1 未跟踪内容分类

当前需要处理：

- `.claude/`；
- `backend/tests/evaluation_results_v2.json`；
- 多份评测和优化 Markdown 文档。

处理策略：

| 类型 | 建议 |
|---|---|
| 正式 Qrels v2 结果 | 提交 |
| 最终架构/部署/评测文档 | 提交 |
| 已过时的优化过程文档 | 移入 `docs/archive/` 或删除 |
| Claude worktree/临时状态 | 加入 `.gitignore`，不提交 |
| 缓存、数据库、上传、模型权重 | 保持忽略 |

不要在未确认内容前执行批量删除；先生成清单并逐项审查。

### 11.2 最终门禁

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

- [ ] Git 只保留明确允许的未跟踪本地文件。
- [ ] Qrels v2 正式结果已跟踪。
- [ ] README 与结果自动校验一致。
- [ ] 后端、前端、静态检查零失败、零 warning。
- [ ] 恢复新逻辑专项测试通过。
- [ ] BM25 chunk ID 集合校验通过。
- [ ] 四模式评测结果已归档。
- [ ] Docker 端到端冒烟通过。
- [ ] OCR/Rerank 降级能力通过容器验证。
- [ ] 创建发布 tag 并记录完整验收结果。

---

## 12. 推荐提交拆分

```text
eval: add validated qrels v2 baseline results
test: validate qrels v2 result and readme consistency
test: cover atomic upload restore and startup recovery
test: cover qdrant restore collection lifecycle
feat: verify bm25 chunk id sets during restore
eval: diagnose keyword and rerank ablation execution
eval: publish ocr and rerank deployment matrix
ci: run full compose end-to-end smoke test
chore: organize release documentation and ignore local agent state
```

每个提交只处理一个主题，恢复、评测和 CI 不应混在同一个大提交中。

---

## 13. 工作量与评分路径

| 优先级 | 工作项 | 预计工作量 | 完成后预期 |
|:---:|---|:---:|:---:|
| P0 | 提交 v2 结果 + README 自动一致性 | 0.25 天 | 9.05 |
| P0 | 恢复新逻辑专项测试 | 1 天 | 9.10 |
| P1 | BM25 chunk ID 集合校验 | 0.5～1 天 | 9.15 |
| P1 | keyword/Rerank 异常诊断 | 0.5 天 | 9.15～9.18 |
| P1 | OCR/Rerank 四模式评测 | 0.5～1 天 | 9.20 |
| P1 | Docker 完整冒烟 | 0.5 天 | 9.20 |
| P2 | 仓库与文档清理 | 0.25 天 | 发布完整度提升 |

预计总工作量：**3～4.5 个开发日**。

---

## 14. 9.2 分验收结论模板

只有以下证据全部存在时，才将项目评为 9.2：

```text
1. master 工作区发布状态清晰；
2. 后端、前端、Ruff、Mypy 全绿；
3. Qrels v2 JSON 已提交且 README 一致；
4. keyword-only 与 Rerank 实际执行状态可证明；
5. OCR/Rerank 四模式有独立报告；
6. 恢复原子切换和 Qdrant 清理有专项故障测试；
7. SQLite/Qdrant/BM25 使用稳定 chunk ID 集合校验；
8. Docker 中认证、上传、检索、SSE、持久化和降级全部通过。
```

最终优化重点不再是增加功能，而是让现有能力具备完整、可重复、可审计的发布证据。
