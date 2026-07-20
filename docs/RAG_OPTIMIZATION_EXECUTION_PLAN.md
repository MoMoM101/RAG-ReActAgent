# RAG 优化方案与执行记录

> 日期：2026-07-14  
> 范围：分块、关键词检索、RRF 融合、qrels 评测与质量门禁  
> 原则：先修评测可信度，再优化召回；在线评测不得静默发送语料到未确认的外部 API。

## 1. 目标与验收标准

本轮不继续堆叠检索功能，而是解决三类基础问题：

1. `section_key` 必须稳定、正确，且与 qrels 使用相同规范。
2. keyword-only 必须具备独立、可重复的离线质量基线。
3. 消融模式必须真正隔离检索来源，不能通过零权重泄漏另一来源的候选。

离线强制门禁：

| 指标 | 下限 |
|---|---:|
| BM25 Hit@5 | 75% |
| BM25 MRR | 70% |
| BM25 NDCG@5 | 70% |

在线消融报告门禁：

| 比较 | 允许退化 |
|---|---:|
| Hybrid NDCG@5 相对 Semantic-only | 不超过 3 个百分点 |
| Rerank NDCG@5 相对 Hybrid | 不超过 2 个百分点 |
| Keyword-only Hit@5 | 不低于 75% |

## 2. 根因

### 2.1 Token 下标被当作字符下标

旧分块器用 tiktoken 的 token offset 查找 Markdown 标题的字符位置。ASCII 文本中不容易暴露，但中文字符经常对应多个 token，后续 chunk 会绑定到错误标题。

### 2.2 overlap 跨越章节边界

即使修正下标，chunk 仍可能从上一节尾部开始，跨过新标题并包含下一节主体。只选择“起始位置之前的标题”或“chunk 内最后一个标题”都会损伤其中一个章节。

### 2.3 qrels 和生产键的规范不一致

生产分块器会把全角冒号等标点规范为 `-`，历史 qrels 中仍保留原标点，导致文本命中但指标判定为未命中。

### 2.4 RRF 零权重仍保留候选

旧 `_rrf_fusion()` 会把被设置为零权重的来源加入结果，只是分数为零。这使 keyword-only/semantic-only 等消融模式不完全隔离。

### 2.5 评测过度宽松

- 历史在线评测把相关性降级到 document-level，同文档所有 chunk 都可能被视为相关。
- NDCG 会把 section-level qrel 同时当成 document-level fallback，造成虚高。
- 五模式矩阵吞掉异常，只验证“函数结束”，不能验证实际成功。
- 未标注相关文档的拒答用例被混入检索指标，会无条件降低 Hit/MRR/NDCG。

## 3. 已执行改动

### 3.1 结构化分块

- Markdown 文档先按标题切成章节。
- 每个章节内部再按 token budget 和自然边界切分。
- overlap 仅发生在同一章节内部。
- 所有 chunk 使用同一套标题键规范化规则。
- 无 Markdown 结构的文档继续使用原有 token 分块流程。

### 3.2 BM25 与融合正确性

- 保留并验证当前工作树中的 BM25 DF 去膨胀修复。
- BM25 结果携带 `document_key`、`section_key`。
- RRF 中权重小于等于零的来源被真正禁用。
- 保留单路故障时的 keyword-only / semantic-only 降级。

### 3.3 可信评测

- 新增完全离线的 BM25 + qrels v2 质量门禁。
- qrels 加载时规范化 section key。
- 修正 NDCG 的 section/document fallback 逻辑。
- 在线评测优先使用审核过的 section-level qrels；没有精确映射的旧查询才使用 section-level legacy annotation，并在报告中记录数量。
- 在线结果写入 `quality_gate.passed` 和具体违规项。
- 需要外部 Embedding/LLM 的五模式矩阵改为显式设置 `RUN_LIVE_RAG_EVAL=1` 后运行，不再默认联网。

## 4. 验证方式

离线核心门禁：

```bash
cd backend
pytest tests/evaluation/test_offline_bm25_quality.py -v
```

完整 RAG 专项：

```bash
cd backend
pytest tests/rag tests/textdb tests/evaluation tests/test_eval_metrics.py -v
```

显式授权后的在线消融：

```bash
cd backend
set RUN_LIVE_RAG_EVAL=1
python tests/evaluate_rag.py --output evaluation_results_v2.json
```

在线评测会把测试语料发送到 `.env` 配置的 Embedding/LLM 服务。运行前必须确认服务归属和数据处理政策。

## 5. 发布与迁移

结构化分块改变了 chunk 边界和 section key，已有索引不会自动获得收益。发布时必须：

1. 备份 SQLite、上传文件和 Qdrant collection。
2. 通过现有 rebuild 流程重建全部文档索引。
3. 重建后运行离线门禁。
4. 经数据出口授权后运行在线消融。
5. 只有 `quality_gate.passed=true` 才更新 README 中的公开质量指标。

如果在线 Hybrid 仍弱于 Semantic-only，下一轮按以下顺序实验，并一次只改一个变量：

1. 开启自适应查询分类权重。
2. 对 BM25 候选增加相对分数阈值，过滤弱关键词命中。
3. 调整 `rrf_semantic_weight / rrf_keyword_weight`。
4. 在独立 dev split 上选择参数，在 test split 上只验证一次。
5. Rerank 无稳定 NDCG 收益时保持默认关闭。

## 6. 本轮不做的事项

- 不根据同一份 test qrels 自动搜索最优权重，避免评测集过拟合。
- 不在未经确认的外部 API 上执行在线语料评测。
- 不修改已有用户文档或直接触发生产索引重建。
- 不以启发式答案相似度代替人工审核的答案正确性评测。
