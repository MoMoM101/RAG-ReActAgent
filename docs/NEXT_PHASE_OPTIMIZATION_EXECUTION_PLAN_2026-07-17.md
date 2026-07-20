# RAG Agent 后续设计与优化执行流程

> 日期：2026-07-17  
> 适用基线：Docker 全链路验收修复后的当前工作区  
> 前置检查点：`docs/DOCKER_E2E_ACCEPTANCE_CHECKPOINT_2026-07-17.md`  
> 文档状态：待按阶段执行  
> 核心原则：先封板当前部署闭环，再做容量、性能和企业化改造；任何性能优化不得降低 Grounded Answer 质量门禁。

## 1. 当前基线与下一阶段判断

当前项目已经具备以下基础能力：

- 真实 Docker 网络中的 frontend、backend、Qdrant 可启动并健康运行。
- 前端已经由 Vite 开发服务器切换为 Nginx 生产静态服务。
- 支持知识库批量上传、后台处理、Qdrant 与 BM25 混合索引。
- 支持管理令牌、SSE 问答、来源展示和 Grounded Answer 校验。
- 已验证容器重启持久化、Qdrant 降级检测和恢复。
- 93 条人工标注问答集的 Grounded Answer 质量已经通过既有质量门禁。
- 本轮 Docker 修复相关回归测试 60/60 通过。

当前不应立刻大规模重构 RAG 算法。下一阶段的正确顺序是：

```text
P0 当前版本封板
  → P1 自动化 Docker 验收
  → P1 性能与容量基线
  → P1 上传处理可靠性
  → P1 RAG 尾延迟优化
  → P2 安全与企业权限
  → P2 数据层与多实例能力
  → P2 可观测性、SLO 与告警
  → P2 灾备演练和发布门禁
  → P3 灰度上线与持续评测
```

## 2. 总体验收指标

后续工作统一使用以下指标判断是否完成，不以“代码写完”作为完成标准。

### 2.1 RAG 质量硬门禁

| 指标 | 最低门槛 | 目标值 |
|---|---:|---:|
| Faithfulness | 不低于当前正式基线 2 个百分点以上 | ≥ 98% |
| 引用精确率 | ≥ 95% | ≥ 98% |
| 引用完整率 | ≥ 95% | ≥ 98% |
| 拒答准确率 | ≥ 98% | 100% |
| 事实召回率 | ≥ 85% | ≥ 88% |
| 回答完成准确率 | ≥ 95% | ≥ 98% |
| 在线生成错误 | 0 | 0 |

### 2.2 在线延迟指标

| 指标 | 阻断阈值 | 目标值 |
|---|---:|---:|
| 可见 TTFT P50 | > 1.5 秒 | ≤ 1.0 秒 |
| 可见 TTFT P95 | > 3.5 秒 | ≤ 2.5 秒 |
| RAG 总耗时 P50 | > 2.5 秒 | ≤ 2.0 秒 |
| RAG 总耗时 P95 | > 6 秒 | ≤ 4 秒 |
| RAG 总耗时 P99 | > 12 秒 | ≤ 8 秒 |
| 缓存命中 P95 | > 1 秒 | ≤ 500 毫秒 |
| SSE 异常中断率 | > 0.5% | < 0.1% |

### 2.3 文档处理指标

| 指标 | 阻断阈值 | 目标值 |
|---|---:|---:|
| 50 文件批量请求成功率 | < 99% | ≥ 99.9% |
| 单文件状态丢失率 | > 0 | 0 |
| ready 后索引一致率 | < 100% | 100% |
| 重启后任务恢复成功率 | < 99% | ≥ 99.9% |
| 失败任务可重试率 | < 100% | 100% |
| Qdrant/BM25 chunk 一致率 | < 100% | 100% |

### 2.4 部署与可靠性指标

| 指标 | 目标 |
|---|---:|
| 健康检查恢复时间 | ≤ 30 秒 |
| 单容器重启数据丢失 | 0 |
| 备份恢复成功率 | 100% |
| RPO | ≤ 24 小时，生产目标可收紧到 1 小时 |
| RTO | ≤ 30 分钟，生产目标 10 分钟 |
| 严重漏洞 | 0 |
| 镜像内明文凭据 | 0 |

## 3. Phase 0：完成当前 Docker 封板

### 3.1 目标

在当前代码上完成最后一次“干净数据备份 → 清空 → 恢复 → 再问答”，形成可发布基线。

### 3.2 执行步骤

1. 按 `DOCKER_E2E_ACCEPTANCE_CHECKPOINT_2026-07-17.md` 第 6 节检查当前 `ragagent-e2e` 栈。
2. 确认 2 份验收文档均为 ready，BM25 与 Qdrant 各有 2 个文档条目。
3. 生成 `backup-final.tar.gz` 并记录 SHA-256。
4. 清空隔离知识库。
5. 调用 `/api/backup/restore` 恢复。
6. 核对 2 份文档、2 个 BM25 chunk、2 个 Qdrant point。
7. 通过 Nginx `/api/chat` 再问一次产品和退款问题。
8. 检查 SSE 包含 `answer_chunk`、`sources`、`verification`、`done`。
9. 运行 Docker smoke，预期 5/5 通过。
10. 将最终结果追加到 Docker 验收报告，而不是只保留终端输出。

### 3.3 代码触点

- `backend/api/backup.py`
- `backend/rag/pipeline.py`
- `backend/tests/e2e/test_docker_smoke.py`
- `docker-compose.yml`
- `docker-compose.e2e.yml`

### 3.4 完成定义

- 新备份在最新版 staging BM25 严格校验下恢复成功。
- 恢复后的真实问答质量三项均为 1.0。
- 当前版本被标记为后续优化的 control baseline。

### 3.5 回滚条件

若恢复失败，不得继续 Phase 1。保留备份、恢复前卷快照和失败日志，优先修复恢复事务与回滚逻辑。

## 4. Phase 1：把人工 Docker 验收自动化

### 4.1 目标

将本轮手工执行的构建、启动、上传、问答、重启、备份恢复和降级检测固化为一次命令，避免下次发布重新人工拼接命令。

### 4.2 设计

新增一个隔离验收脚本，例如：

```text
scripts/docker_e2e_acceptance.ps1
```

脚本使用固定 Compose project 名加随机运行后缀：

```text
ragagent-e2e-<timestamp>
```

流程：

```text
config 校验
  → build
  → up -d
  → 等待 healthy
  → 镜像敏感文件检查
  → 鉴权检查
  → 批量上传固定夹具
  → 等待 ready
  → BM25/Qdrant 一致性检查
  → SSE 问答与事件断言
  → restart 持久化
  → backup/clear/restore
  → Qdrant stop/start 降级恢复
  → 输出 JSON + Markdown 报告
  → 可配置是否 down -v
```

### 4.3 产物

- `scripts/docker_e2e_acceptance.ps1`
- `backend/tests/e2e/fixtures/`
- `artifacts/docker-e2e/<run-id>/result.json`
- `artifacts/docker-e2e/<run-id>/report.md`
- CI 中的 Docker acceptance job。

### 4.4 必须具备的保护

- 必须显式检查 project name 以 `ragagent-e2e-` 开头后才允许 `down -v`。
- 不读取或输出 API Key 明文。
- 报告仅记录配置是否存在、哈希和脱敏后的 provider/model。
- 每个等待循环都有超时和最后状态输出。
- 失败时默认保留容器和卷，便于诊断。

### 4.5 验收

- 同一提交连续运行 3 次全部通过。
- 每次都生成结构化结果和 Markdown 报告。
- 失败时退出码非 0，并指出具体失败阶段。
- CI 不允许用 skip 把服务不可达伪装成通过。

## 5. Phase 2：建立真实容量与性能基线

### 5.1 目标

回答三个实际问题：

1. 一次上传 50 个文件能否稳定完成？
2. 多用户同时问答时，延迟和错误率如何变化？
3. 性能瓶颈来自模型、embedding、SQLite、Qdrant、BM25，还是前端代理？

### 5.2 测试矩阵

#### 文档上传矩阵

| 场景 | 文件数 | 单文件大小 | 总大小 |
|---|---:|---:|---:|
| 小文件批量 | 50 | 10 KB–1 MB | ≤ 50 MB |
| 中等文件批量 | 20 | 5–20 MB | 100–400 MB |
| 大文件边界 | 1–5 | 接近前端设置上限 | 动态计算 |
| 混合格式 | 30 | PDF/DOCX/TXT/MD/CSV/XLSX | 变化 |
| 部分非法 | 10 | 含扩展名错误、空文件和重复文件 | 变化 |

#### 问答并发矩阵

| 并发 | 持续时间 | 目的 |
|---:|---:|---|
| 1 | 10 分钟 | 单用户基线 |
| 5 | 15 分钟 | 小团队使用 |
| 10 | 20 分钟 | 部门级试点 |
| 25 | 20 分钟 | 单实例压力边界 |
| 50 | 10 分钟 | 过载与保护策略 |

### 5.3 采集指标

- HTTP 总耗时、TTFT、SSE 完成耗时。
- `rag_intent`、`rag_retrieval`、`rag_draft_generation`、`rag_verification`、`rag_total`。
- embedding 批次大小、外部 API 等待时间、429/5xx 比例。
- 文档队列深度、排队时间、处理时间、失败与重试次数。
- SQLite busy/locked 次数和事务时长。
- Qdrant query/upsert P50/P95/P99。
- CPU、内存、磁盘 IOPS、网络和容器重启次数。

### 5.4 代码触点

- `backend/metrics.py`
- `backend/tracing.py`
- `backend/rag/retriever.py`
- `backend/rag/pipeline.py`
- `backend/worker/tasks.py`
- `backend/api/chat.py`
- `backend/api/documents.py`
- `docs/grafana_v4_rag_dashboard.json`

### 5.5 验收产物

- `docs/CAPACITY_BASELINE_REPORT_<date>.md`
- 原始 JSON/CSV 指标文件。
- 推荐单实例最大并发、最大批量和资源规格。
- 明确的扩容触发点，而不是笼统结论。

## 6. Phase 3：文档处理可靠性与吞吐优化

### 6.1 目标

让 50 文件批量上传在失败、重启、重复点击和网络断开场景下仍能正确显示并最终收敛。

### 6.2 设计项

#### 任务幂等

- 以 `document_id + file_hash + generation_id` 作为处理幂等边界。
- 同一文档只允许一个 active ingestion job。
- 重试前检查是否已有 committed generation。
- stale job 只能清理自己写入的 generation，不得按 document_id 删除 active 数据。

#### 队列治理

- 区分 pending、running、retry_wait、failed、dead_letter、completed。
- 心跳超时只重新入队未完成任务。
- 指数退避加随机抖动。
- embedding 429、网络超时和业务错误分别处理。
- 设置单任务最大尝试次数和人工重放入口。

#### 状态可见性

- 上传接口立即返回所有文件名、document id 和初始状态。
- 前端按 document id 合并进度事件，禁止用“全局 uploading”锁死整个上传区。
- 每个文件可独立重试、删除和重新上传。
- SSE 断开后使用轮询恢复，不依赖切换页面触发刷新。

#### 索引一致性

- ready 前必须通过 Qdrant/BM25 chunk id 集合一致性检查。
- generation commit 与 document active pointer 切换必须有清晰事务边界。
- 启动恢复只处理非终态文档。

### 6.3 测试

- 上传处理中重启 backend。
- embedding 完成、Qdrant 写入后重启。
- BM25 写入后、generation commit 前重启。
- 同一文件连续点击上传两次。
- 50 文件中 3 个非法、2 个重复、1 个超限。
- 前端 SSE 中断后恢复页面状态。

### 6.4 验收

- 50 文件状态全部可追踪。
- 不出现“上传完成，等待处理”永久卡死。
- 不出现 ready 文档无 BM25 或无 Qdrant point。
- 删除、重试、重新上传按钮在终态可用。
- 重启后任务只恢复一次。

## 7. Phase 4：RAG 尾延迟优化

### 7.1 目标

以当前质量通过结果为硬约束，重点降低 P95/P99 和用户可见 TTFT，而不是只降低平均值。

### 7.2 优化顺序

1. **缓存命中路径**：问题规范化 + collection version + model/config fingerprint。
2. **意图识别优化**：明确知识库问题减少不必要 LLM 路由。
3. **检索并行化**：semantic、BM25 和必要的 query rewrite 并发执行。
4. **候选裁剪**：在不降低召回的前提下减少进入生成上下文的无关 chunk。
5. **修复预算**：确定性引用格式修复优先，只有不忠实内容才触发第二次 LLM。
6. **流式安全单元**：按已校验句子或列表项发送，避免完整答案生成后才首屏可见。
7. **超时预算**：为意图、检索、生成、验证、修复分别设置预算。
8. **慢请求分类**：按 external API、retrieval、repair、context size 分桶。

### 7.3 不允许的优化

- 关闭 verifier。
- 隐藏引用缺失提示。
- 降低支持阈值换取通过。
- 将失败样本从评测集中删除。
- 只报告均值，不报告 P95/P99。
- 把 rescore 结果伪装为新的在线生成结果。

### 7.4 A/B 执行

每个优化单独形成一个 feature flag：

```text
control
  vs cache
  vs retrieval parallelism
  vs source pruning
  vs deterministic repair
  vs combined optimized
```

先用 10–20 条定向样本排除回归，再运行完整 93 条在线评测。保存每条问题的答案、来源、verification、TTFT 和总耗时。

### 7.5 验收

- 质量硬门禁全部通过。
- P95/P99 达到第 2.2 节目标或至少比 control 降低 20%。
- 二次 LLM repair 触发率和接受率都有数据解释。
- 慢请求 Top 10 有明确原因，不允许只给“网络波动”结论。

## 8. Phase 5：安全和企业权限

### 8.1 目标

从当前单一管理令牌模式升级为可供公司多人使用的身份、权限和审计体系。

### 8.2 分层设计

#### 身份认证

- 支持 OIDC/OAuth2，对接企业 IdP。
- 短时 access token，刷新与撤销机制。
- 禁止将管理令牌长期存储在浏览器明文存储中。

#### 权限模型

建议最小角色：

| 角色 | 能力 |
|---|---|
| viewer | 问答、查看允许访问的来源 |
| editor | 上传、重试和删除自己范围内文档 |
| knowledge_admin | 管理知识库、重建索引 |
| system_admin | 配置、备份恢复、监控和用户管理 |

#### 数据隔离

- 文档、chunk、conversation、message、cache、task 全部携带 tenant/workspace id。
- Qdrant payload 和 BM25 查询必须强制 tenant filter。
- 备份按租户或明确的全局管理员权限执行。
- 来源下载接口再次做权限校验，不能只依赖前端隐藏。

#### 审计

记录登录、上传、删除、清空、重建、备份、恢复、配置修改、权限修改和敏感来源访问。

### 8.3 安全工程

- Compose secrets 或外部 secret manager，避免普通环境变量长期承载生产密钥。
- 镜像使用非 root 用户。
- 固定基础镜像版本或 digest。
- 生成 SBOM，执行依赖和镜像漏洞扫描。
- Nginx 增加合理 CSP、HSTS（启用 HTTPS 后）、请求体和速率策略。
- 日志与 tracing 对问题、来源文本和凭据做脱敏。

### 8.4 验收

- 未授权跨租户查询为 403，且检索结果为 0。
- 普通用户不能调用备份、恢复、重建和系统配置接口。
- 所有高风险操作有 actor、时间、对象、结果和 request id。
- 镜像无高危/严重漏洞，或有书面例外与到期时间。

## 9. Phase 6：数据层与多实例部署

### 9.1 决策边界

若仅单机、单租户、小团队内部试点，SQLite 可以继续使用，但必须保持单 backend writer 和可靠备份。

若需要以下任一能力，应迁移 PostgreSQL：

- 多 backend 副本。
- 多租户并发写入。
- 更强事务、锁和审计查询。
- 高可用、主从、托管备份。
- 独立 worker 扩容。

### 9.2 推荐目标架构

```text
Nginx / Ingress
  → Backend API × N
  → Worker × N
  → PostgreSQL
  → Qdrant cluster / managed Qdrant
  → Object storage
  → Redis（缓存、分布式锁或队列，可按需要引入）
```

### 9.3 迁移原则

- 先抽象数据库能力并建立迁移脚本，再切换数据库。
- 使用正式 schema migration 工具管理版本。
- 文件从本地卷迁移到对象存储前，先统一 storage interface。
- 不在一次发布中同时迁移数据库、对象存储和任务队列。
- 双写阶段必须有一致性核对和明确退出条件。

### 9.4 验收

- 两个 backend 副本并行运行时无重复处理和数据竞争。
- 任一 backend/worker 被杀死后任务可恢复。
- 数据库迁移可向前执行，也有经过演练的回滚/恢复方案。
- 备份恢复在目标数据层重新通过。

## 10. Phase 7：可观测性、SLO 与告警

### 10.1 指标层

至少覆盖：

- HTTP 请求量、错误率、P50/P95/P99。
- SSE 打开数、断开数、done 完成率。
- RAG 各阶段耗时。
- cache hit/miss。
- repair reason、repair trigger、repair accepted。
- 文档队列深度、最老任务年龄、重试和 dead-letter。
- embedding/LLM 外部调用状态码和延迟。
- Qdrant、数据库、磁盘和容器资源。
- 备份年龄、最近恢复演练时间和结果。

### 10.2 日志层

- 全链路 request id、conversation id、document id、generation id、task id。
- 日志禁止输出 API Key 和完整 Authorization。
- 默认不记录完整用户问题、答案和来源正文；需要时使用受控采样。

### 10.3 Trace 层

建议 span：

```text
chat.request
  ├─ intent
  ├─ retrieval.semantic
  ├─ retrieval.bm25
  ├─ retrieval.rerank
  ├─ llm.draft
  ├─ verify
  ├─ repair
  └─ persistence
```

### 10.4 SLO 与告警

初始建议：

- 月可用性 99.5%，稳定后提升到 99.9%。
- 5 分钟错误率 > 2% 告警。
- SSE done 完成率 < 99.5% 告警。
- P95 > 6 秒持续 10 分钟告警。
- 最老 ingestion task > 10 分钟告警。
- dead-letter 增长立即告警。
- 备份超过 26 小时未成功告警。

## 11. Phase 8：灾备、发布门禁与供应链

### 11.1 灾备流程

- 定时备份 SQLite/PostgreSQL、上传文件、active collection 指针和必要配置元数据。
- Qdrant 使用快照或可重复重建策略。
- 备份加密、校验 SHA-256、设置保留周期。
- 每月至少一次自动恢复到隔离环境。
- 每季度一次人工灾备演练。

### 11.2 发布门禁

PR/日常 CI：

- 后端 lint、类型检查、全量测试。
- 前端 lint、测试、生产构建。
- Docker build。
- Compose smoke。
- 备份安全与恢复集成测试。
- secret scan、dependency scan、image scan。

正式发布：

- 完整 93 条人工标注在线评测，禁止仅 rescore。
- control/optimized 指纹与当前代码一致。
- 质量和延迟门禁通过。
- Docker acceptance 自动化通过。
- 数据迁移和回滚演练通过。
- 生成 release evidence bundle。

### 11.3 发布证据包

建议包含：

```text
release-evidence/<version>/
  git-commit.txt
  image-digests.json
  sbom/
  vulnerability-report.json
  unit-test-report.xml
  frontend-test-report.xml
  docker-e2e-report.md
  grounded-answer-eval.json
  capacity-baseline.json
  migration-plan.md
  rollback-plan.md
```

## 12. Phase 9：灰度上线与持续优化

### 12.1 灰度顺序

1. 开发环境。
2. 隔离验收环境。
3. 内部 5–10 人试用。
4. 单部门试点。
5. 10% 流量或用户灰度。
6. 50% 灰度。
7. 全量。

### 12.2 每级观察窗口

- 最短 24 小时。
- 检查错误率、TTFT、P95/P99、引用质量、拒答、上传失败、任务积压和用户反馈。
- 任一硬门禁破坏立即回滚，不带问题进入下一级。

### 12.3 持续评测

- 每周从匿名化真实请求抽取候选样本。
- 人工复核后加入版本化评测集。
- 对错误类型分桶：检索失败、来源错误、引用缺失、不忠实、误拒答、超时、工具异常。
- 每次优化必须说明改善了哪个桶，是否让其他桶回归。

## 13. 推荐执行排期

| 周期 | 阶段 | 主要产出 |
|---|---|---|
| 第 1–2 天 | Phase 0 | 当前版本最终封板报告 |
| 第 3–5 天 | Phase 1 | 一键 Docker 验收脚本和 CI job |
| 第 2 周 | Phase 2 | 50 文件与问答并发容量报告 |
| 第 3 周 | Phase 3 | ingestion 幂等、恢复与前端状态闭环 |
| 第 4 周 | Phase 4 | RAG P95/P99 优化与 93 条在线复测 |
| 第 5–6 周 | Phase 5 | OIDC/RBAC/审计和安全加固 |
| 第 7–8 周 | Phase 6 | PostgreSQL/对象存储可行性与首阶段迁移 |
| 第 9 周 | Phase 7–8 | SLO、告警、灾备和发布证据包 |
| 第 10 周 | Phase 9 | 内部灰度和阶段性结论 |

排期以单人或小团队为参考。数据库、多租户和企业身份接入应根据实际公司基础设施调整。

## 14. 每轮优化的标准工作模板

后续每一轮都遵循同一流程：

```text
1. 固定 control 代码、配置、模型和数据集
2. 记录当前质量、延迟、错误和资源基线
3. 只选一个主要瓶颈
4. 写清假设和预期收益
5. 增加失败复现或回归测试
6. 在 feature flag 下实现
7. 运行定向测试
8. 运行模块完整测试
9. 运行 Docker 全链路验收
10. 运行完整在线质量评测（需要时）
11. 比较 control 与 optimized
12. 做上线、继续观察或回滚决定
13. 将命令、结果、异常和结论写入仓库文档
```

每轮报告必须回答：

- 优化前问题是什么？
- 用什么证据证明它是瓶颈？
- 修改了哪些文件和配置？
- 哪些指标改善，改善多少？
- 哪些指标下降，为什么？
- 是否通过质量硬门禁？
- 是否可回滚，回滚方法是什么？
- 下一步是什么？

## 15. 优先级结论

近期最值得投入的不是继续增加 RAG 功能，而是以下四项：

1. 完成最终备份恢复封板。
2. 将 Docker 验收自动化并接入 CI。
3. 用 50 文件批量和真实并发建立容量基线。
4. 在质量不下降的条件下继续降低 RAG P95/P99。

完成这四项后，项目才适合从“单机可用、内部试点”进入“可重复发布、可度量扩容”的阶段。若目标是公司多部门或对外服务，则 OIDC/RBAC、租户隔离、PostgreSQL、多实例 worker、审计和灾备属于上线前必做项，而不是后续可选优化。
