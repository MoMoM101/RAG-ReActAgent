# RAG 答案可信度与引用闭环优化方案

> 日期：2026-07-14  
> 范围：知识库回答的引用绑定、忠实度校验、安全降级与端到端评测

## 1. 背景与目标

当前检索层已经具备 section-level 稳定键和可信的离线 BM25 基线，但回答层仍有三个缺口：

1. 检索结果没有面向回答的稳定引用编号，模型无法可靠地把结论绑定到证据。
2. 现有验证器仅统计答案与所有来源的词汇重合，不能判断“某条声明引用的证据是否支持它”。
3. 验证发生在 `done` 之后且不持久化，前端和历史消息无法稳定展示验证状态。

本轮目标是建立一个不依赖外部评判模型、可离线回归的最小可信闭环：

`检索结果 → 稳定引用 ID → 带引用回答 → 声明级验证 → SSE/历史记录展示`

## 2. 设计原则

- **证据可追溯**：每个来源携带 `citation_id`、`document_key`、`section_key`。
- **验证可解释**：返回每条声明、引用和不支持原因，而不只返回单一分数。
- **数字优先保护**：声明中的数字、版本、日期等必须能在所引证据中找到。
- **默认兼容**：默认 `report` 模式只报告风险，不改写已经生成的答案。
- **严格模式显式开启**：`strict` 模式在低于门槛时追加安全提示；不静默编造替代答案。
- **本地可测**：核心指标和决策逻辑不调用 LLM 或网络。

## 3. 数据契约

`search_docs` 的每条结果新增：

```json
{
  "citation_id": "S1",
  "document_key": "deployment-guide",
  "section_key": "python-version",
  "filename": "deployment.md",
  "text": "..."
}
```

知识库回答中的关键事实采用 `[S1]` 或 `[S1, S2]` 引用。验证事件在 `done` 之前发送：

```json
{
  "status": "verified",
  "claim_count": 3,
  "supported_claims": 3,
  "faithfulness": 1.0,
  "citation_precision": 1.0,
  "citation_recall": 1.0,
  "unsupported_claims": []
}
```

## 4. 验证规则

1. 将答案按中英文句末和列表项切成声明，过滤标题、问题和明显的元话语。
2. 解析每条声明中的 `[S<n>]` 引用。
3. 只有引用存在、引用证据与声明达到词汇覆盖门槛，且声明中的数字均出现在证据中，才视为支持。
4. 未引用声明可以对全部来源做保守支持检测，但仍计为 citation recall 缺失。
5. 状态门槛：
   - `verified`：faithfulness 与 citation recall 均不低于配置门槛；
   - `partial`：存在部分支持；
   - `unverified`：没有声明得到支持；
   - `no_sources`：知识库回答没有来源。

## 5. 配置与降级

| 配置 | 默认值 | 说明 |
|---|---:|---|
| `GROUNDING_VERIFICATION_ENABLED` | `true` | 是否运行本地验证 |
| `GROUNDING_MIN_COVERAGE` | `0.70` | verified 最低忠实度 |
| `GROUNDING_ENFORCEMENT` | `report` | `off` / `report` / `strict` |

`strict` 模式低于门槛时追加“部分内容未获来源充分支持”的安全提示，并保留原回答与详细验证结果供审计。

## 6. 验收标准

- 来源 ID 在工具结果、SSE sources 和历史消息之间保持一致。
- 无效引用不能计入 citation precision。
- 缺少引用的事实声明会降低 citation recall。
- 引用证据缺少声明中的数字时，该声明不得通过。
- `verification` 事件必须先于 `done`。
- 无检索工具的普通对话不被错误标记为知识库回答失败。
- 后端 verifier、Agent、API 专项测试及前端类型检查通过。

## 7. 后续在线验收

本轮先建立确定性离线门禁。生产级验收需在授权的数据出口上重建索引并运行答案集，至少报告：answer correctness、faithfulness、citation precision、citation recall、abstention accuracy 和端到端延迟 P50/P95。

## 8. 执行与验证记录

已完成：

- 检索结果补齐唯一 `citation_id`、chunk/document/section 稳定键。
- 同一回答内多次 `search_docs` 的来源统一编号、聚合和去重。
- Prompt 强制知识库事实使用 `[S<n>]` 引用。
- verifier 升级为声明级证据验证，输出 faithfulness、citation precision、citation recall 和不支持声明。
- 数字不一致、引用不存在、无引用声明均有独立判定。
- verification SSE 事件调整到 `done` 之前，并持久化至消息记录。
- 前端支持来源编号、章节和验证状态展示。

验证结果：

- Agent/RAG/evaluation 回归：`281 passed, 7 skipped`。
- 新增引用与验证专项：`15 passed`。
- 前端消息组件：`11 passed`。
- 前端生产构建与 lint：通过。
- Ruff：通过。
- Mypy：通过。
