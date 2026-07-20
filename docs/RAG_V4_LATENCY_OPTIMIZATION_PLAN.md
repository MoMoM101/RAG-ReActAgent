# RAG V4 低延迟优化实施方案

> 日期：2026-07-14  
> 状态：待实施  
> 前置版本：Grounded Answer V3.3  
> 核心原则：降低用户等待时间，但不得降低已经通过的 Grounding 工业质量门禁

## 1. 背景与问题定义

V3.3 已在 93 条端到端在线评测集上通过质量门禁：

| 指标 | V3.3 全量结果 | 工业门槛 |
|---|---:|---:|
| Faithfulness | 98.48% | 不低于对照组 |
| 引用精确率 | 97.73% | ≥95% |
| 引用完整率 | 98.18% | ≥95% |
| 拒答准确率 | 100.00% | ≥98% |
| 事实召回率 | 89.47% | ≥85% |
| 答案完成准确率 | 98.92% | ≥95% |
| 在线生成错误 | 0 | 0 |

质量已经达到内部试点标准，但当前链路以“完整缓冲后再返回”为代价保证 Grounding，用户会在整个生成和可能发生的第二次生成完成后才看到答案。

完整 93 题 V3.3 延迟分布如下：

| 指标 | 当前值 |
|---|---:|
| 平均延迟 | 3349 ms |
| P50 | 2518 ms |
| P90 | 5829 ms |
| P95 | 7824 ms |
| P99 | 15651 ms |
| 超过 5 秒 | 16/93 |
| 超过 10 秒 | 4/93 |

该评测延迟主要统计答案生成与纠正阶段。生产请求还会叠加意图识别、知识库检索、重排、网络传输和消息持久化，因此实际端到端等待可能更长。

## 2. 当前链路与根因

### 2.1 当前流程

```text
用户请求
  → 意图识别
  → search_docs
  → 混合检索/重排
  → LLM 完整生成草稿（不向前端发送）
  → verify_answer 声明级校验
  → needs_grounding_repair 判断
      ├─ 不需要纠正：一次性发送完整答案
      └─ 需要纠正：第二次 LLM 完整生成
            → 比较原稿与纠正稿
            → 一次性发送较优答案
  → 发送 sources / verification / done
```

### 2.2 延迟根因

1. **首字被完整答案阻塞**：有知识库来源时，`backend/agent/loop.py` 会缓冲最终答案，TTFT 基本等于首次完整生成时间。
2. **纠正成本接近一次完整生成**：引用格式问题、少量漏引和真实不忠实声明目前都可能触发相同的第二次 LLM 调用。
3. **缺少修复原因分级**：系统没有区分“纯格式问题”“可确定性修复问题”“必须重新生成问题”。
4. **缺少分阶段耗时指标**：当前主要看到 HTTP 总耗时和工具耗时，无法直接知道草稿、校验、确定性修复、LLM 纠正分别占多少。
5. **复杂问题输出较长**：跨文档、比较、总结类问题包含多个事实，生成时长和触发纠正的概率都更高。
6. **重复请求没有答案级缓存**：同一用户或多个用户查询相同问题时仍会重新执行完整链路。

## 3. V4 目标与不可破坏约束

### 3.1 性能目标

| 指标 | V4 目标 | 阻断上线阈值 |
|---|---:|---:|
| 可见首字时间 TTFT P50 | ≤1000 ms | >1500 ms |
| 可见首字时间 TTFT P95 | ≤2500 ms | >3500 ms |
| 端到端总耗时 P50 | ≤2000 ms | >2500 ms |
| 端到端总耗时 P95 | ≤5000 ms | >6000 ms |
| 端到端总耗时 P99 | ≤10000 ms | >12000 ms |
| LLM 二次纠正触发率 | ≤10% | >15% |
| LLM 二次纠正接受率 | ≥40% | <20% 时应收紧触发条件 |
| 缓存命中请求 P95 | ≤500 ms | >1000 ms |

### 3.2 质量硬约束

任何延迟优化都必须同时满足：

- Faithfulness 不得低于 V3.3 超过 2 个百分点，且不得低于对照组。
- 引用精确率 ≥95%。
- 引用完整率 ≥95%。
- 拒答准确率 ≥98%。
- 事实召回率 ≥85%，且相对对照组下降不超过 2 个百分点。
- 答案完成准确率 ≥95%。
- 不得把未验证声明提前发送给用户。
- 不得通过关闭 verifier、降低支持阈值或隐藏失败记录换取速度。

## 4. V4 目标架构

```text
用户请求
  → 检索与重排
  → 缓存查询
      ├─ 命中：校验缓存版本/文档版本 → 流式返回
      └─ 未命中：LLM 流式生成
                    ↓
                原子单元缓冲器
             （一句/一个列表项）
                    ↓
                快速声明校验
                    ├─ 已支持且引用正确 → 立即发送该单元
                    ├─ 仅引用格式问题 → 确定性修复后发送
                    └─ 不忠实/误拒答 → 暂停发送，最多一次 LLM 纠正
                                                ↓
                                      只生成尚未提交的剩余内容
  → sources / verification / metrics / done
```

V4 不再把整个回答视为不可拆分的大块，而是引入“已提交单元”和“未提交单元”的边界。已经发送给前端的内容必须通过校验，后续纠正只能处理尚未发送的内容，不能修改已提交事实。

## 5. 分阶段实施流程

## Phase 0：可观测性与基线冻结

### 5.1 目标

先获得每个阶段的真实耗时和纠正原因，否则无法判断优化收益来自哪里，也无法发现质量保护逻辑被误绕过。

### 5.2 新增计时点

在 `backend/agent/loop.py`、`backend/api/chat.py` 和检索工具中记录：

- `rag_intent_ms`：意图识别耗时。
- `rag_retrieval_ms`：search_docs 总耗时。
- `rag_rerank_ms`：重排耗时。
- `rag_llm_ttft_ms`：首次模型调用首 token 时间。
- `rag_draft_generation_ms`：首次草稿完整生成时间。
- `rag_verification_ms`：声明校验时间。
- `rag_deterministic_repair_ms`：确定性修复时间。
- `rag_llm_repair_ms`：第二次模型纠正时间。
- `rag_visible_ttft_ms`：用户真正看到首个 `answer_chunk` 的时间。
- `rag_total_ms`：从请求进入到 `done` 的总时间。

### 5.3 新增计数器

- `rag_grounding_repair_trigger_total{reason=...}`。
- `rag_grounding_repair_accept_total{reason=...}`。
- `rag_deterministic_repair_total{type=...}`。
- `rag_answer_cache_total{result=hit|miss|stale}`。
- `rag_stream_unit_total{result=verified|repaired|held|dropped}`。
- `rag_full_refusal_total{result=accepted|rechecked|converted_partial}`。

### 5.4 纠正原因枚举

`needs_grounding_repair` 不再只返回布尔值，改为结构化决策：

```python
GroundingDecision(
    action="accept | deterministic_repair | llm_repair | refuse",
    reasons=[
        "missing_citation",
        "invalid_citation",
        "redundant_citation",
        "unsupported_claim",
        "missing_number",
        "topical_false_refusal",
        "coverage_recheck",
    ],
    verification=verification_result,
)
```

### 5.5 验收

- 指标端点可以查看上述分段 P50/P95/P99。
- 每条评测记录保存是否触发纠正、触发原因、纠正是否被采用以及两次生成耗时。
- 使用同一 V3.3 配置重新跑 93 题，质量结果与现有报告一致，性能数据仅增加观测字段。

## Phase 1：确定性引用修复，减少第二次 LLM 调用

### 5.6 可确定性修复的范围

以下问题不需要重新调用模型：

1. 引用位于句号之后，移动到句号之前。
2. 同一引用在一个原子声明中重复出现，去重。
3. 引用了不存在的来源编号，删除无效编号，但不得自动认定声明受支持。
4. 一个声明含多个引用，其中部分引用不支持声明；保留能够完整支持该声明的最小引用集合。
5. 声明没有引用，但 verifier 能找到唯一且高置信度的支持来源时，追加该来源。

### 5.7 自动补引的安全条件

自动补引必须同时满足：

- 声明本身被证据直接支持。
- 所有数字、日期、版本号均存在于候选来源中。
- 最佳候选支持分数 ≥0.55。
- 最佳候选比第二候选至少高 0.15，或只有一个候选达到阈值。
- 不涉及比较结论、因果、优劣、适用场景、最高级或推导步骤。
- 不修改声明正文，只允许修改引用标记和标点。

不满足任一条件时，禁止自动补引，应进入 LLM 纠正或安全拒答。

### 5.8 建议代码结构

新增：

- `backend/agent/grounding_repair.py`
  - `classify_grounding_failure(...)`
  - `repair_citation_format(...)`
  - `repair_atomic_claim_citations(...)`
  - `select_minimal_supporting_sources(...)`
  - `GroundingDecision`
  - `GroundingRepairResult`

保留 `backend/agent/verifier.py` 为纯校验模块，避免 verifier 同时承担修改职责。

### 5.9 验收

- 引用格式类样本不触发第二次 LLM。
- 确定性修复后必须再次调用 verifier，且结果优于原稿才可采用。
- 自动修复单测覆盖数字冲突、错误来源、多个候选分数接近、比较推导等拒绝修复场景。
- 93 题二次 LLM 触发率相对 V3.3 至少下降 40%。

## Phase 2：收紧 LLM 纠正触发与预算

### 5.10 仅保留两类 LLM 纠正

第二次 LLM 调用只用于：

1. **真实不忠实声明**：存在无法由任何来源直接支持的事实，且不能只删除该声明而保持回答完整。
2. **高置信度误拒答**：回答整体拒绝，但查询中的核心实体与来源高度相关，来源至少支持一个可独立回答的事实。

以下情况不再触发 LLM 纠正：

- 纯引用位置或标点问题。
- 可唯一定位证据的漏引。
- Markdown 小标题、结构标签或限制说明。
- 已经达到质量门槛的单一事实定义回答。覆盖扩展改为独立低优先级策略，不阻塞首字。

### 5.11 纠正时间预算

- 每个请求最多纠正一次。
- 纠正调用最大预算建议 4000 ms。
- 纠正前检查 `max_total_time` 剩余时间；不足 1500 ms 时不再启动第二次模型。
- 纠正超时后采用已验证的安全部分，不回退到包含未支持声明的原稿。
- 为纠正请求设置更短的最大输出长度，只生成需要替换或补充的部分。

### 5.12 可选快速纠正模型

增加独立配置：

```env
GROUNDING_REPAIR_ENABLED=true
GROUNDING_REPAIR_MODEL=
GROUNDING_REPAIR_TIMEOUT=4
GROUNDING_REPAIR_MAX_TOKENS=350
GROUNDING_REPAIR_MAX_ATTEMPTS=1
```

当 `GROUNDING_REPAIR_MODEL` 为空时使用主模型；配置快速模型时，必须单独通过 93 题质量评测，不能默认认为小模型能够正确修复引用。

### 5.13 验收

- 二次纠正触发率 ≤10%。
- 纠正接受率 ≥40%；接受率过低说明触发条件仍过宽。
- P95 至少下降 20%。
- 所有 V3.3 质量门槛继续通过。

## Phase 3：原子单元校验与安全流式输出

### 5.14 原子单元定义

流式缓冲器按以下边界切分：

- 中文句号、问号、感叹号。
- Markdown 列表项换行。
- 空行分段。
- 引用后的句末标点。

标题、`已确认：`、`无法确认：` 等结构行可以与下一事实单元一起缓冲，不能单独作为事实发送。

### 5.15 状态机

```text
COLLECTING
  → 收到完整原子单元
  → VERIFYING
      ├─ VERIFIED：发送 answer_chunk，记录为 COMMITTED
      ├─ FORMAT_ONLY：确定性修复 → 再验证 → 发送
      ├─ UNSUPPORTED：不发送该单元，进入 REPAIRING
      └─ INCOMPLETE：继续收集 token

REPAIRING
  → 向模型提供：原问题、来源、已提交事实、未提交草稿
  → 要求只输出剩余内容
  → 对新单元重复 VERIFYING
```

### 5.16 关键安全规则

- 已提交内容不可撤回、替换或重复发送。
- LLM 纠正提示必须包含已经提交的事实，要求不要重复这些事实。
- 任何未完成句子都不能发送。
- 引用必须与同一原子单元一起发送，不能先发送事实再补发引用。
- 前端断线时立即取消尚未提交的生成或纠正任务。
- 若单元边界无法可靠判断，回退到整段缓冲，不冒险发送。

### 5.17 SSE 事件建议

保留现有事件兼容性，并扩展：

```text
status: 正在检索资料...
status: 正在核对来源...
answer_chunk: 已验证的原子单元
sources: 来源列表
verification: 最终聚合校验结果
done: 完成
```

不建议引入前端“撤回已发送答案”的 `answer_replace` 机制，因为它会增加 UI 状态复杂度，并可能让用户短暂看到不可信内容。

### 5.18 验收

- TTFT 统计使用第一个真实 `answer_chunk`，不能用 `status` 事件代替。
- 所有发送出的原子事实均可在对应引用中直接找到。
- 模拟慢速模型时，用户可在完整答案结束前看到已验证的第一条事实。
- 断线、超时、空响应、半句结束和引用跨 chunk 等测试全部通过。

## Phase 4：版本感知的答案缓存

### 5.19 缓存键

缓存键至少包含：

```text
tenant/user_scope
+ normalized_query
+ conversation_context_hash
+ retrieval_result_hash
+ document_collection_version
+ prompt_version
+ model_name
+ grounding_policy_version
```

个人记忆问题、权限敏感文档和依赖长对话上下文的问题不得跨用户共享缓存。

### 5.20 缓存值

- 最终答案。
- 来源及引用编号。
- verification 结果。
- 文档集合版本。
- 创建时间、过期时间。
- 生成模型和 Prompt 版本。

### 5.21 失效规则

- 文档新增、删除、重新切分或集合重建后立即失效。
- Prompt、verifier 或模型版本变化后失效。
- 默认 TTL 建议 5–15 分钟，先从短 TTL 开始。
- 缓存命中后仍校验用户权限和文档可见性。

### 5.22 验收

- 缓存结果不能引用当前用户无权访问的来源。
- 集合重建后旧答案无法命中。
- 相同安全查询缓存命中 P95 ≤500 ms。
- 监控命中率、失效率和陈旧缓存拒绝次数。

## Phase 5：上下文与输出长度优化

该阶段在前四阶段完成后执行，避免过早改变检索质量。

### 5.23 来源裁剪

- 生成上下文只保留最终 top-k 来源，不把候选池全部发送给模型。
- 同一文档连续、重叠度高的 chunk 可合并去重。
- 保留标题、章节和父级上下文，避免裁剪导致语义丢失。
- 对答案级可答样本重新确认 Hit@k，不能只看平均检索耗时。

### 5.24 输出预算

- 简单事实：1–3 个原子事实。
- 定义/概览：定义 + 代表例子 + 必要限制，通常不超过 5 条。
- 比较/跨来源：每一方 1–3 条事实 + 无法确认维度。
- 总结类问题允许更长输出，但设置明确最大 token。

输出预算只删除重复和无关信息，不能省略人工标注的关键事实。

### 5.25 并行化边界

- 独立的关键词检索与向量检索继续并行。
- 不并行启动两个完整答案模型，以免成本翻倍且增加选择复杂度。
- 可以在首次模型生成期间预计算来源 token、数字索引和候选支持矩阵，为流式 verifier 降低开销。

## 6. 配置设计

建议在 `backend/config.py` 与 `.env.example` 增加：

```env
# Grounding fast path
GROUNDING_STREAM_VERIFY_ENABLED=false
GROUNDING_DETERMINISTIC_REPAIR_ENABLED=true
GROUNDING_AUTO_CITE_MIN_SCORE=0.55
GROUNDING_AUTO_CITE_MIN_MARGIN=0.15

# Bounded LLM repair
GROUNDING_REPAIR_ENABLED=true
GROUNDING_REPAIR_MODEL=
GROUNDING_REPAIR_TIMEOUT=4
GROUNDING_REPAIR_MAX_TOKENS=350
GROUNDING_REPAIR_MAX_ATTEMPTS=1

# Answer cache
RAG_ANSWER_CACHE_ENABLED=false
RAG_ANSWER_CACHE_TTL_SECONDS=300
RAG_ANSWER_CACHE_MAX_ENTRIES=1000

# Performance gates
RAG_TTFT_P95_TARGET_MS=2500
RAG_TOTAL_P95_TARGET_MS=5000
```

所有高风险新能力默认使用 feature flag。确定性修复可先开启；原子流式校验和缓存必须经过专项测试后再灰度开启。

## 7. 代码改造清单

| 文件/模块 | 改造内容 |
|---|---|
| `backend/agent/loop.py` | 分阶段计时、原子单元缓冲状态机、纠正预算、取消传播 |
| `backend/agent/verifier.py` | 输出结构化失败原因，保持纯校验职责 |
| `backend/agent/grounding_repair.py` | 新增确定性引用修复与最小证据选择 |
| `backend/api/chat.py` | 最终 verification 聚合、SSE 时序与持久化一致性 |
| `backend/config.py` | V4 feature flags、阈值和超时配置 |
| `backend/metrics.py` | RAG 分段耗时、纠正率、缓存指标 |
| `backend/tracing.py` | 增加 retrieval/draft/verify/repair/stream span |
| `backend/tests/run_grounded_answer_eval.py` | 保存 TTFT、修复原因、修复次数和分段延迟 |
| `frontend/src/stores/chatStore.ts` | 确认原子 answer_chunk 顺序与断线恢复 |
| `frontend/src/components/chat/MessageBubble.tsx` | 保持流式 Markdown 和引用渲染稳定 |

## 8. 测试方案

### 8.1 单元测试

- 引用在句号前后的位置归一化。
- 无效、重复、冗余引用处理。
- 唯一高置信来源自动补引。
- 多个候选接近时拒绝自动补引。
- 数字不一致时禁止自动修复。
- 比较、因果、最高级声明禁止自动补引。
- Markdown、代码块、表格、中文/英文标点的原子单元切分。
- 已提交单元不能被纠正过程重复输出。
- 纠正超时后只返回已验证安全内容。

### 8.2 集成测试

- search_docs → 生成 → 单元校验 → SSE → 保存消息完整链路。
- `grounding_enforcement=off|report|strict` 三种模式。
- 客户端中途断开、LLM 超时、工具超时、数据库保存失败。
- 缓存命中、缓存过期、集合重建、权限变化。
- 多请求并发下 citation_id 不串线。

### 8.3 性能测试

分别测试：

1. 无修复简单事实。
2. 确定性引用修复。
3. LLM 二次纠正。
4. 长总结与跨来源回答。
5. 缓存命中。
6. 5、10、20 并发。

每组输出 TTFT、总耗时、模型调用次数、输入/输出 token、纠正率和质量指标。

### 8.4 端到端质量评测顺序

1. verifier、repair、流式状态机单测。
2. 12 条 V3.1/V3.3 聚焦集。
3. 所有历史失败题和 P95 慢题组成的 V4 性能集。
4. 完整 93 题在线评测。
5. 300–500 条真实业务人工标注集。
6. 并发压测和灰度流量观测。

## 9. V4 专项评测集

从现有全量结果固定以下慢题进入回归集：

- `cross-005`：15651 ms。
- `multi-hop-003`：13715 ms。
- `instruct-004`：11197 ms。
- `cn-en-mix-002`：10338 ms。
- `short-002`：7824 ms。
- `exact-009`：7765 ms。
- `cross-003`：7283 ms。

同时加入：

- 引用缺失但存在唯一支持来源。
- 多来源冗余引用。
- 数字冲突。
- 部分可答但首稿整体拒答。
- 真正不可答且实体与来源相关。
- 长答案中间出现一个不受支持声明。
- 模型流式 chunk 在引用中间断开。

## 10. 灰度发布流程

### 10.1 发布阶段

1. **Shadow**：V4 与 V3.3 同时计算，但仅向用户返回 V3.3；比较质量和耗时。
2. **内部账号**：仅开发、测试和标注人员使用 V4。
3. **5% 流量**：观察至少 2 个工作日。
4. **25% 流量**：观察 P95、错误率、纠正率和用户取消率。
5. **50% 流量**：确认缓存权限隔离与并发稳定性。
6. **100% 流量**：连续达标后全量。

### 10.2 每阶段晋级条件

- 质量硬门槛全部通过。
- HTTP 5xx 和 SSE 中断率无显著上升。
- P95 延迟相对 V3.3 至少下降 20%。
- 用户主动取消率不升高。
- 无跨用户缓存泄漏、引用串线或已发送内容被覆盖。

### 10.3 自动回滚条件

任一条件满足立即关闭相应 feature flag：

- 引用精确率或完整率低于 95%。
- 拒答准确率低于 98%。
- 出现未验证声明提前发送。
- 缓存返回越权来源。
- P95 连续 15 分钟高于 V3.3 基线 20%。
- SSE 错误率或请求超时率显著上升。

回滚顺序：

1. 关闭答案缓存。
2. 关闭原子流式校验，恢复整段缓冲。
3. 关闭确定性修复，恢复 V3.3 LLM 纠正。
4. 必要时整体切回 V3.3。

## 11. 实施优先级与预计收益

| 优先级 | 工作项 | 风险 | 预期收益 |
|---|---|---|---|
| P0 | 分段指标与纠正原因 | 低 | 找到真实瓶颈，避免盲目优化 |
| P0 | 确定性引用修复 | 中 | 大幅减少第二次 LLM 调用 |
| P0 | 收紧 LLM 纠正条件和预算 | 中 | 改善 P90/P95/P99 |
| P1 | 原子单元校验流式输出 | 高 | 显著改善用户感知 TTFT |
| P1 | V4 慢题专项回归 | 低 | 防止长尾回归 |
| P2 | 版本感知答案缓存 | 中高 | 重复查询接近即时返回 |
| P2 | 上下文去重与输出预算 | 中 | 降低 token、成本和长题耗时 |

建议先完成 P0。P0 不改变前端协议，回滚简单，而且能够直接验证“二次生成是否为主要长尾来源”。原子流式校验涉及 SSE 时序和不可撤回内容，应在 P0 收益验证后独立实施。

## 12. 完成定义

V4 只有在以下条件全部满足时才算完成：

- 93 条全量评测质量门禁通过。
- 300–500 条真实业务人工标注集质量门禁通过。
- TTFT P50 ≤1000 ms、P95 ≤2500 ms。
- 总耗时 P50 ≤2000 ms、P95 ≤5000 ms、P99 ≤10000 ms。
- LLM 二次纠正触发率 ≤10%。
- 10 并发下无引用串线、缓存越权或 SSE 状态错乱。
- 完成 Shadow、内部账号和至少 5% 灰度。
- 监控面板、报警规则、feature flag 和回滚演练齐备。

达到以上条件后，RAG 才从“质量合格但体感偏慢”升级为“可在公司业务中规模化使用”。
