# 评测方法与复现说明

本文说明公开指标的适用范围、数据来源、复现方式和已知局限。首页数字不是通用 RAG 能力保证，也不能直接外推到未见过的真实业务文档。

## 公开评测组成

| 评测 | 文档 | 查询 | 数据性质 | 主要用途 |
|---|---:|---:|---|---|
| `rag-agent-eval-v2` | 4 | 93 | 人工编写的合成通用文档与显式 qrels | 回答忠实度、引用、拒答、事实召回和延迟 |
| `rag-agent-complex-disambiguation-v2` | 9 | 29 | 人工编写的支付、传感器、合规类合成文档与显式 qrels | 复杂、混淆和跨文档检索 |
| Docker E2E fixtures | 2 | 固定验收问题 | 人工编写的产品与政策文本 | 上传、索引、问答、重启和恢复链路 |

93 条回答评测包括精确字段、短查询、跨文档、数值、中英混合、改写、长查询、否定、不可回答、歧义、比较、提示词注入、拼写错误和边界输入。部分旧条目没有显式 `answerability` 字段，由 qrels schema 根据预期事实和相关性按兼容规则推断；加载后的有效分布为 61 条 `full`、5 条 `partial`、27 条 `none`，即正式报告中的 66 条可回答、27 条不可回答。

数据与来源文件：

- `backend/tests/qrels_data_v2.json`
- `backend/tests/qrels_complex_v2.json`
- `backend/tests/eval_data/`
- `backend/tests/DATA_PROVENANCE.md`

所有公开语料均为合成数据。支付、合规、药品和传感器内容只是业务风格夹具，不代表真实企业、合同、产品承诺或医疗建议。

## 标注和完整性

评测文档、查询、相关章节、预期事实、必须引用项和可回答性标签由项目维护者人工编写与复核。qrels 使用稳定的 `document_key` / `section_key`，不使用待评测运行结果自动生成 ground truth。

正式报告记录数据集、评估器、校验器、提示词或语料的 SHA-256。检索评测要求 `qrels_fallback_count=0`。这些措施可以发现文件漂移，但不能消除单人标注偏差；当前项目没有多人标注一致性数据。

## train / dev / test 边界

当前 93 条数据中定义了固定的失败聚焦 dev 子集，用于提示词、拒答和修复策略迭代；这些子集仍属于同一个 `rag-agent-eval-v2` 数据集。当前首页的 93 条正式结果使用了全量数据，因此它不是严格隔离、在优化期间不可见的 held-out test。

据此，98.48% 应解释为：

> 当前代码和提示词在固定合成基准上的受控在线评测结果。

不应解释为：

> 对任意真实文档或未见查询都能达到 98.48%。

后续计划冻结独立 test 和真实脱敏 business-holdout；在完成之前，项目不会把现有结果描述成独立泛化成绩。

## 环境准备

```powershell
cd backend
python -m pip install -r requirements.txt
python -m pip install -r requirements-dev.txt
Copy-Item .env.example .env
```

完整回答评测需要在 `backend/.env` 中配置可用的 OpenAI 兼容 LLM。复杂检索评测还需要配置 Embedding，并准备评测脚本所需的 Qdrant 和可选 Reranker。模型服务、硬件、网络和缓存状态会影响延迟；重新运行的文本结果也可能因模型服务的非确定性发生变化。

## 一键复现命令

以下命令均从 `backend` 目录执行。

### 1. 无模型调用的数据与 BM25 冒烟检查

```powershell
python -m tests.run_grounded_answer_eval --dry-run
```

该命令检查 93 条 qrels 是否可加载、评测索引能否构建以及已标注查询是否命中，不产生首页的回答质量数字。

### 2. 重新运行 93 条回答质量评测

```powershell
python -m tests.run_grounded_answer_eval `
  --output tests/grounded_answer_eval_reproduced.json `
  --max-model-calls 200 `
  --concurrency 2 `
  --enforce-gate
```

该评测对 control 和 optimized 各运行一次，最多消耗 200 次模型调用。建议写入新的 `*_reproduced.json`，不要直接覆盖仓库中的正式报告。

仅验证固定失败聚焦开发集：

```powershell
python -m tests.run_grounded_answer_eval --dev --output tests/grounded_answer_eval_dev.json
```

### 3. 重新运行 29 条复杂检索评测

```powershell
python tests/complex_eval_runner.py
```

该脚本使用固定参数运行 semantic-only、keyword-only、hybrid-no-rerank 和 hybrid-rerank。当前脚本会生成 `tests/evaluation_results_complex_v2.json`；复现正式报告前应先保留已有文件或在独立工作树中运行。

### 4. 校验正式发布报告

```powershell
python release_gate.py
```

发布门禁校验报告新鲜度、provenance、质量阈值和性能阈值。它只验证现有报告，不能代替重新调用模型。

## 指标实现

| 指标 | 实现 |
|---|---|
| Precision / Recall / Hit@K、MRR、NDCG | `backend/tests/eval_metrics.py` |
| 预期事实覆盖、引用和拒答基础评分 | `backend/tests/eval_answer.py` |
| 在线回答评测、门禁和报告 provenance | `backend/tests/run_grounded_answer_eval.py` |
| 复杂检索运行与固定参数 | `backend/tests/complex_eval_runner.py` |
| 回答证据校验 | `backend/agent/verifier.py` |

## 消融结果

29 条复杂检索报告中的 Top-5 消融如下：

| 配置 | Precision@5 | MRR | Hit@5 | NDCG@5 | 平均延迟 |
|---|---:|---:|---:|---:|---:|
| Semantic only | 33.79% | 94.75% | 96.55% | 90.72% | 254.86 ms |
| Keyword only | 27.59% | 79.60% | 89.66% | 75.99% | 55.72 ms |
| Hybrid, no rerank | 34.48% | 97.70% | 100.00% | 93.17% | 940.76 ms |
| Hybrid + rerank | 34.48% | 97.70% | 100.00% | 93.17% | 943.17 ms |

在这组小型合成数据上，混合检索优于单路检索；Reranker 没有带来可见质量提升，只增加了少量延迟。不能据此断言 Reranker 对更大或真实语料无效。

回答评测的 control 与 optimized 对比是组合策略实验：optimized 同时包含选择性重试、确定性引用修复和受限 LLM 修复，因此不能被解读为单一组件的因果消融。

## 失败案例

正式 optimized 报告并非所有样本满分：

- `cross-005`：“为什么说数据预处理比调参更重要，模型部署时需要哪些框架支持”；
- 数据只分别支持“预处理方法”和“FastAPI 可用于在线推理”，没有直接支持“为什么预处理比调参更重要”这一因果判断；
- optimized 版本选择整体拒答，导致该样本忠实度、引用精确率、引用召回率和预期事实召回均为 0；
- 这暴露了当前策略对“问题一部分可回答、一部分证据不足”的处理仍过于保守，应优先回答有证据部分并明确保留项。

此外，optimized 组有 20 条记录的预期事实召回未达到 100%，总体 expected fact recall 为 87.47%。这说明 98.48% 的引用类指标不能替代事实覆盖率，后续优化应单独追踪跨文档、比较、否定和部分可回答问题的遗漏。
