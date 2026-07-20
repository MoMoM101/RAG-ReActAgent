# RAG Agent 9.1 后续优化与生产验收计划

> 适用范围：单机、单租户、内网或中小流量生产部署  
> 制定日期：2026-07-13  
> 当前基线：Ruff 通过、Mypy 136 个源文件通过、全量测试 `433 passed, 4 skipped`  
> 目标：在不提前引入完整多租户复杂度的前提下，把当前发布候选提升为可验证、可恢复、可观测的稳定单租户生产版本。

---

## 1. 范围与结论

### 1.1 本轮必须完成

1. Qdrant 与 BM25 的 generation 原子可见性闭环。
2. 固定语料、固定 qrels 的真实 RAG 质量评测。
3. 后台任务重启恢复与幂等重放。
4. LLM 首 Token、单次调用和 Agent 总流程 deadline。
5. Docker 全新环境端到端冒烟。
6. OCR/Rerank 四组合真实性验证。
7. 故障注入、容量测试、指标与告警闭环。

### 1.2 本轮明确不做

- 不实施完整用户体系、租户管理、租户计费和租户级配额。
- 不强制迁移 PostgreSQL、Redis、S3/MinIO 或 Qdrant Cluster。
- 不以“配置项存在”代替模型真正加载和链路实际生效。
- 不以单元测试通过代替检索质量、容量和故障恢复报告。

### 1.3 多租户策略

当前保持单租户，但新接口禁止继续写死无法演进的全局假设：

- 在服务层定义 `RequestScope` 或等价上下文，当前固定 `tenant_id="default"`。
- 检索、索引、任务和文件服务可以接受 scope，但本阶段不改变现有数据布局。
- 禁止在未完成数据库、Qdrant、BM25、文件和缓存全链路隔离前宣传多租户能力。
- 预留接口不得成为当前发布阻塞项，也不得引入大量无效字段和复杂迁移。

---

## 2. 当前基线和主要缺口

| 领域 | 当前能力 | 主要缺口 | 优先级 |
|---|---|---|:---:|
| 后台任务 | 状态持久化、heartbeat、陈旧任务识别 | 不能跨进程自动重放 | P0 |
| 混合检索 | semantic/BM25 单路降级 | 跨存储写入不是原子可见 | P0 |
| 关键词检索 | 元数据与 DF/upsert 已修复 | 正式 qrels 结果尚需重跑 | P0 |
| Agent | 调用和工具超时、基础 verifier | 首 Token 和总流程 deadline 未完全闭环 | P0 |
| OCR/Rerank | 可选部署并有兜底 | 缺少四组合真实性报告 | P1 |
| 可观测性 | request id、metrics 基础结构 | 缺少抓取、仪表盘、告警和非零验证 | P1 |
| 部署 | Docker 配置存在 | 缺少全新环境端到端证据 | P1 |
| 容量 | 功能测试较完整 | 缺少并发、规模、P95/P99 和成本报告 | P1 |
| 多租户 | 当前未实现 | 本阶段只预留演进上下文 | 非阻塞 |

---

## 3. 执行总流程

```text
冻结基线
  -> generation 原子索引
  -> 任务幂等重放
  -> Deadline 与取消传播
  -> 固定数据集质量评测
  -> OCR/Rerank 四组合
  -> Docker E2E
  -> 故障注入与容量压测
  -> 可观测性和告警
  -> 发布验收与回滚演练
```

执行规则：

- 每个阶段必须独立提交，禁止把结构迁移、功能修改和大规模格式化混在一起。
- 每个阶段先补失败测试，再实现，再跑专项和全量门禁。
- 任一 Gate 未通过，不进入依赖它的下一阶段。
- 所有评测产物必须记录 Git commit、配置摘要、语料版本、模型状态和时间戳。

---

## 4. Phase A：冻结可复现基线

### A1. 工作项

- 固定 Python、Node、Docker、Qdrant 和主要依赖版本。
- 保存当前全量测试、Ruff、Mypy 输出。
- 为评测语料生成内容哈希，为配置生成脱敏摘要。
- 记录 embedding、LLM、OCR、Rerank 的 provider、model 和 loaded 状态。
- 确认 `.env.example` 与实际配置字段同步，但不得记录密钥。

### A2. 建议产物

```text
backend/tests/baselines/
  release_9_1_manifest.json
  rag_dataset_manifest.json
  dependency_snapshot.txt
```

`release_9_1_manifest.json` 至少包含：

```json
{
  "commit": "<git-sha>",
  "python": "3.12.x",
  "tests": {"passed": 433, "skipped": 4},
  "embedding_model": "<model>",
  "llm_model": "<model>",
  "ocr_requested": true,
  "ocr_loaded": true,
  "rerank_requested": true,
  "rerank_loaded": true,
  "dataset_sha256": "<sha256>"
}
```

### Gate A

- [ ] 同一 commit 在干净环境中可复现测试结果。
- [ ] 配置摘要不包含 API Key、Token 或连接密码。
- [ ] 语料和 qrels 都有稳定版本标识。

---

## 5. Phase B：Generation 原子索引可见性

这是当前 RAG 可靠性的最高优先级。目标不是让 Qdrant 和 SQLite 变成真正的分布式事务，而是保证检索永远只看到完整提交的一代索引。

### B1. 状态模型

建议 generation 至少具有以下状态：

```text
PREPARING -> WRITING_VECTOR -> WRITING_BM25 -> VERIFYING -> COMMITTED
                                                   \-> FAILED
```

必须保存：

- `generation_id`
- `document_id`
- `status`
- `expected_chunk_count`
- `vector_chunk_count`
- `bm25_chunk_count`
- `chunk_ids_hash`
- `created_at`、`committed_at`
- `error_stage`、`error_message`

### B2. 写入流程

1. 创建 `PREPARING` generation。
2. 切分文档并一次性确定稳定 `chunk_id` 集合。
3. 向 Qdrant 写入带 `generation_id` 的 payload。
4. 向 BM25 写入相同 `generation_id` 和相同 `chunk_id`。
5. 分别读取两侧 ID，比较数量、集合哈希和缺失项。
6. 只在完全一致时把 generation 标为 `COMMITTED`。
7. 在同一数据库事务中切换文档的 `active_generation_id`。
8. 检索只接受 active 且 committed 的 generation。
9. 新 generation 提交后再异步清理旧 generation。

### B3. 失败处理

- Qdrant 写入失败：generation 标记失败，删除本代已写向量。
- BM25 写入失败：保留旧 active generation，本代标记失败并清理。
- 校验不一致：禁止提交，记录缺失 chunk 样本。
- 进程在提交前崩溃：启动时识别非 committed generation 并清理或续作。
- 清理旧 generation 失败：不得影响当前 committed generation 可用性，进入维护队列重试。

### B4. 必测故障点

- 写入第一个 Qdrant batch 后崩溃。
- Qdrant 完成、BM25 尚未开始时崩溃。
- BM25 写入一半时崩溃。
- VERIFYING 阶段发现单个 chunk 缺失。
- COMMITTED 后旧 generation 清理失败。
- 同一文档连续触发两次重建。

### Gate B

- [ ] 任意故障点下，检索仍只读到旧完整 generation 或新完整 generation。
- [ ] 不会同时混合两代 chunk。
- [ ] Qdrant/BM25 chunk ID 集合校验真实执行，而非仅比较写入调用是否成功。
- [ ] 重复索引同一文档不会造成 DF 膨胀或重复向量。

---

## 6. Phase C：后台任务幂等重放

### C1. 任务数据模型

任务记录需要从“运行历史”升级为“可执行命令”：

- `task_type`
- `payload_json`
- `idempotency_key`
- `status`
- `attempt`
- `max_attempts`
- `next_run_at`
- `heartbeat_at`
- `worker_id`
- `last_error`

禁止保存不可序列化对象、打开的文件句柄或已经创建的 coroutine。

### C2. 重放机制

- 建立显式 handler registry：`task_type -> async handler`。
- 启动时把 heartbeat 超时的 running 任务改为 retryable，而不是直接永久失败。
- 使用原子 claim 防止同一任务被两个 worker 同时执行。
- handler 必须使用幂等键：重复执行不得重复建文档、重复计数或泄漏旧索引。
- 采用有上限的指数退避，并区分永久错误与暂时错误。
- 达到最大次数进入 dead-letter 状态，由管理接口人工重试。

### C3. 当前单机边界

SQLite 模式只保证单服务实例或单 worker 安全。多实例竞争执行属于未来迁移到 PostgreSQL/专用队列后的范围。

### Gate C

- [ ] 任务执行中强制终止进程，重启后能够继续或安全重做。
- [ ] 相同幂等键重复提交只产生一个最终业务结果。
- [ ] dead-letter 可查询、可审计、可人工重试。
- [ ] shutdown 后无未等待协程和悬挂 heartbeat。

---

## 7. Phase D：Deadline、取消和资源释放

### D1. 超时层级

建议满足：

```text
单次连接超时 < 单次 LLM/Embedding 超时 < 工具超时 < Agent 总 deadline
```

需要覆盖：

- LLM 建连超时。
- 流式首 Token 超时。
- 流式相邻 chunk 空闲超时。
- LLM 总响应超时。
- Embedding 单批次超时。
- Rerank/OCR 超时。
- 单工具超时。
- Agent 分类、记忆加载、循环、最终合成的总 deadline。

### D2. 取消传播

- SSE 客户端断开后取消 Agent、工具、检索和 LLM 请求。
- `CancelledError` 必须继续向上传播，不得被宽泛 `except Exception` 转为普通失败。
- `finally` 中释放流、HTTP response、数据库 session 和 heartbeat。
- 对有副作用的工具，超时不等于操作未发生；必须配合幂等键或状态查询。

### Gate D

- [ ] 模拟 LLM 永不返回首 Token，请求在配置时间内结束。
- [ ] 客户端断开后，后台不继续消耗模型 Token。
- [ ] 超时、取消和供应商错误有不同的指标标签。
- [ ] 全量测试无协程、连接或资源泄漏警告。

---

## 8. Phase E：真实 RAG 质量评测

### E1. 数据集要求

- 第一阶段不少于 200 条真实业务问题。
- 覆盖精确关键词、同义表达、长问题、多跳问题、数字/日期、否定问题和无答案问题。
- 每条问题标注相关 `document_key`、`section_key` 或 `chunk_id`。
- 单独保留至少 30 条中文关键词检索专项样本。
- qrels 不依赖数据库临时 UUID，优先使用稳定业务键。

### E2. 固定评测矩阵

| 模式 | Semantic | BM25 | Rewrite | Rerank |
|---|:---:|:---:|:---:|:---:|
| keyword-only | 关 | 开 | 关 | 关 |
| semantic-only | 开 | 关 | 关 | 关 |
| hybrid | 开 | 开 | 关 | 关 |
| hybrid+rewrite | 开 | 开 | 开 | 关 |
| full | 开 | 开 | 开 | 开 |

每种模式记录 Recall@5/10、MRR@10、NDCG@10、Hit Rate、空结果率、P50/P95/P99。

### E3. 建议发布阈值

- keyword-only 不允许全零，稳定键映射成功率必须为 100%。
- hybrid 的 Recall@10 不得低于 semantic-only 和 keyword-only 中的较优者超过 1 个百分点。
- full 模式 MRR/NDCG 应相对 hybrid 有可解释提升；若没有提升，需要调查模型加载和排序变化。
- 无答案问题不得通过拼接无关 chunk 伪造高命中。
- 新版本关键指标不得较固定基线下降超过预设阈值。

### E4. 评测结果真实性

结果文件必须包含：

- commit 和 dataset hash
- 配置快照
- 每个后端是否实际调用
- OCR/Rerank requested、loaded、fallback reason
- 每条查询的候选与最终排序
- 失败样本分类，而不仅是聚合均值

### Gate E

- [ ] 五种模式均能独立运行并生成非伪造报告。
- [ ] keyword-only 正式结果不再全零。
- [ ] Rerank 开启时可观察到真实顺序变化或明确说明无变化原因。
- [ ] 失败样本已经按切分、召回、融合、重排和标注问题分类。

---

## 9. Phase F：OCR 与 Rerank 四组合验证

必须完成：

1. OCR Off / Rerank Off。
2. OCR On / Rerank Off。
3. OCR Off / Rerank On。
4. OCR On / Rerank On。

OCR 数据集至少包含扫描 PDF、旋转图片、低清图片、中英混排、表格和原生文本 PDF。记录页级成功率、降级页数、处理耗时和内存峰值。

Rerank 需要记录模型是否加载、候选数、排序变化率、耗时和 fallback reason。模型不可用时系统继续运行属于正确设计，但必须在日志、指标和评测报告中可见。

### Gate F

- [ ] 四组合全部完成，不因可选模型缺失导致主链路不可用。
- [ ] OCR 和 Rerank 的真实加载状态可观察。
- [ ] 降级结果有明确原因，不静默伪装完整能力。

---

## 10. Phase G：Docker 全新环境 E2E

### G1. 流程

1. 使用空数据目录构建镜像。
2. 启动依赖并等待健康检查。
3. 验证未认证和已认证接口。
4. 上传原生 PDF 和扫描 PDF。
5. 等待 ingestion 完成并查询任务状态。
6. 执行 keyword、semantic、hybrid 和 Agent 问答。
7. 验证引用可以映射回文档与章节。
8. 重启服务，确认文档、索引和任务状态仍可用。
9. 执行备份、清空、恢复和恢复后检索。
10. 删除文档并验证数据库、Qdrant、BM25 和文件均清理。

### G2. 建议命令

```powershell
docker compose config
docker compose build --no-cache
docker compose up -d
docker compose ps
docker compose logs --no-color
```

### Gate G

- [ ] 全新机器不依赖开发者本地缓存即可启动。
- [ ] 健康检查、上传、索引、检索、问答、重启和恢复全部成功。
- [ ] 容器日志无密钥、未等待协程和持续异常重试。
- [ ] OCR/Rerank 缺失时兜底路径通过；存在时完整路径通过。

---

## 11. Phase H：故障注入与容量压测

### H1. 故障注入矩阵

| 故障 | 预期行为 |
|---|---|
| Qdrant 不可用 | 降级 BM25，记录 keyword-only fallback |
| BM25 锁定/损坏 | 降级 semantic，记录 semantic-only fallback |
| 两路同时不可用 | 明确返回 RetrievalError，不伪装空结果 |
| Embedding 超时 | 关键词路径继续可用 |
| Rerank 超时 | 保留 RRF 排序 |
| OCR 模型失败 | 可提取文本继续，扫描页标记 degraded |
| LLM 中断 | 请求可控结束，资源释放 |
| ingestion 进程崩溃 | generation 不暴露半成品，任务可恢复 |

### H2. 容量梯度

- chunks：1 万、10 万、目标生产规模。
- 并发：1、10、50；如计划更高流量再增加 100。
- 文档：小文件、大 PDF、批量上传和 OCR 密集型文件。
- 测试运行至少包含预热阶段和稳定测量阶段。

### H3. 记录指标

- 检索和 Agent P50/P95/P99。
- 吞吐、错误率、超时率、降级率。
- CPU、内存、磁盘增长和 Qdrant/BM25 大小。
- LLM、Embedding、OCR、Rerank 调用次数和成本估算。
- ingestion 队列长度、处理耗时和失败重试。

### 建议初始 SLO

SLO 最终应根据硬件和业务调整，首轮可使用：

- API 非模型接口成功率 >= 99.9%。
- 检索错误率 < 1%。
- 正常依赖条件下检索 P95 <= 2 秒。
- Agent P95 由实际模型建立基线，不使用拍脑袋绝对值。
- fallback 持续出现必须告警，不把“成功返回”视为完整健康。

### Gate H

- [ ] 每种故障均符合预期降级或明确失败语义。
- [ ] 达到目标数据量和并发时无持续内存增长。
- [ ] P95/P99、吞吐和成本均形成报告。
- [ ] 容量上限和建议部署规格已经写入运维文档。

---

## 12. Phase I：可观测性与告警闭环

### I1. 必须打通的指标

- HTTP：请求量、状态码、P50/P95/P99。
- Agent：循环次数、总耗时、deadline、loop limit、verifier 结果。
- LLM：首 Token、总耗时、Token 数、错误类别、provider/model。
- Tool：调用量、成功率、超时率、重试和副作用状态。
- Retrieval：semantic/BM25 数量、融合数量、fallback reason、各阶段延迟。
- Ingestion：队列长度、任务状态、generation 状态、恢复次数。
- OCR/Rerank：requested、loaded、degraded、error、耗时。

### I2. 标签控制

禁止将 query、document_id、user_id、完整 URL 或错误文本直接作为 Prometheus label，避免高基数和敏感信息泄漏。详细信息进入结构化日志，指标只使用受控枚举。

### I3. 告警建议

- 检索错误率连续 5 分钟 > 1%。
- keyword-only 或 semantic-only fallback 异常增长。
- Agent deadline > 1%。
- ingestion backlog 持续增长。
- generation 长时间停留在非终态。
- OCR/Rerank requested 但长期未 loaded。
- 任务 dead-letter 数量大于 0。

### Gate I

- [ ] Prometheus 能抓取真实非零指标。
- [ ] 单次请求可通过 request id 串联 API、Agent、LLM、Tool 和 Retrieval 日志。
- [ ] 至少完成一次告警触发与恢复演练。
- [ ] 运维人员能够从告警定位到对应运行手册。

---

## 13. 测试与代码门禁

每个阶段至少执行：

```powershell
python -m ruff check backend --config pyproject.toml
python -m mypy backend --config-file pyproject.toml
Set-Location backend
python -m pytest -q
```

专项门禁建议新增：

```text
backend/tests/rag/test_generation_visibility.py
backend/tests/rag/test_retrieval_fallbacks.py
backend/tests/worker/test_task_recovery.py
backend/tests/agent/test_deadlines.py
backend/tests/e2e/test_docker_smoke.py
backend/tests/evaluation/test_quality_matrix.py
```

合并要求：

- Ruff 零错误。
- Mypy 零错误。
- 全量测试通过且无新增 warning。
- 新增故障路径必须有测试。
- 数据库迁移必须同时验证旧库升级和新库初始化。
- 评测指标回归必须有明确审批，不能静默更新 baseline。

---

## 14. 实施顺序与预估工作量

| 顺序 | 阶段 | 预估 | 是否阻塞单租户正式发布 |
|:---:|---|:---:|:---:|
| 1 | Phase A 基线冻结 | 0.5 天 | 是 |
| 2 | Phase B generation 原子可见性 | 2～3 天 | 是 |
| 3 | Phase C 任务幂等重放 | 1.5～2 天 | 是 |
| 4 | Phase D deadline 与取消 | 1～1.5 天 | 是 |
| 5 | Phase E 真实 RAG 评测 | 1～3 天，取决于标注 | 是 |
| 6 | Phase F OCR/Rerank 四组合 | 1 天 | 是 |
| 7 | Phase G Docker E2E | 0.5～1 天 | 是 |
| 8 | Phase H 故障与容量 | 1～2 天 | 是 |
| 9 | Phase I 指标与告警 | 1～2 天 | 建议是 |
| 10 | 多租户预留接口 | 0.5 天 | 否 |

总工程量约 10～16 人日，不包含 200 条真实问题的业务标注沟通时间。

---

## 15. 回滚策略

### 15.1 代码回滚

- 每阶段独立 commit 和变更说明。
- 配置开关必须保留上一条稳定路径。
- 不使用破坏性 Git 操作覆盖未提交用户修改。

### 15.2 索引回滚

- generation 提交后保留上一代至少一个清理周期。
- active generation 切换失败时继续使用旧代。
- 禁止先删除旧索引再构建新索引。

### 15.3 数据库回滚

- schema 变更优先使用向前兼容的新增列和新增表。
- 数据迁移前自动备份数据库并验证备份可读。
- 回滚程序不得删除无法确认归属的数据。

### 15.4 模型回滚

- OCR、Rerank、Embedding 和 LLM 模型变更均保留旧配置。
- 模型切换必须绑定评测报告，失败时恢复旧模型和旧索引 generation。

---

## 16. 最终发布验收清单

### 功能与质量

- [ ] keyword-only、semantic-only、hybrid、rewrite、rerank 正式评测完成。
- [ ] keyword-only 不再全零，稳定键映射率 100%。
- [ ] Agent 引用可定位到真实文档内容。
- [ ] OCR/Rerank 四组合均有真实运行证据。

### 一致性与恢复

- [ ] 检索只读取 committed generation。
- [ ] Qdrant/BM25 chunk 集合一致。
- [ ] ingestion 任意阶段崩溃不暴露半成品。
- [ ] 后台任务重启后可以幂等重放。

### 稳定性

- [ ] 首 Token、调用和 Agent 总 deadline 生效。
- [ ] 客户端断开能够取消下游工作。
- [ ] 单路检索故障降级、双路故障明确失败。
- [ ] 全量测试、Ruff、Mypy、Docker E2E 通过。

### 运维

- [ ] P95/P99、吞吐、资源和成本报告完成。
- [ ] Prometheus 指标真实非零。
- [ ] 告警和回滚至少演练一次。
- [ ] 备份恢复和索引回滚经过验证。

---

## 17. 完成后的定位与评分

完成 Phase A～I 后，项目可以合理定位为：

> 稳定的单机、单租户 RAG Agent 生产版本，具备检索降级、原子索引可见性、任务恢复、质量回归、可观测性和可演练回滚能力。

预计评分可由当前约 `9.1/10` 提升到 `9.3～9.4/10`。评分不会因为增加更多配置项而提升，只依据真实数据评测、故障恢复、容量报告和端到端运行证据提升。

完整多租户、跨节点任务队列、外部数据库、Qdrant 集群和对象存储属于下一阶段的企业级扩展，不影响当前单租户版本完成生产验收。
