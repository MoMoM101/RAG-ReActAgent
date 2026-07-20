# 发布质量门禁

## 目标

正式发布不能只依赖单元测试。发布候选版本还必须提供一份完整、近期、可追溯的在线问答评测，并证明当前代码对应的质量与性能均达标。

## 两级门禁

### 日常 CI

- Ruff、Mypy；
- 后端全量测试和覆盖率；
- 前端 lint、测试和生产构建；
- 备份安全测试；
- Backend Docker 和完整 Compose 冒烟。

Docker 镜像只有在上述任务全部成功后才允许推送。

### 正式发布门禁

执行：

```powershell
cd backend
python release_gate.py
```

门禁要求：

- 使用完整人工标注数据集，control/optimized 都不得缺题、重复或包含生成错误；
- 评测时间不超过 30 天；
- `qrels_data_v2.json`、`agent/verifier.py`、评测脚本和优化提示词指纹与当前代码完全一致；
- 引用精确率、引用完整率、拒答准确率、事实召回率和回答完成率满足质量阈值；
- TTFT、P50/P95/P99 总耗时和修复调用比例满足性能阈值。

任何一项不满足，命令返回非零退出码，标签发布工作流随即失败。

## 生成新的正式评测

服务模型配置完成后运行：

```powershell
cd backend
python -m tests.run_grounded_answer_eval `
  --output tests/grounded_answer_eval_v4_final.json `
  --concurrency 2 `
  --top-k 5 `
  --enforce-gate
```

评测脚本会写入：

- `scoring_version`；
- `evaluation_scope`；
- 数据集、校验器、评测器和优化提示词 SHA-256；
- 质量门禁与性能门禁；
- 每个问题的原始答案、来源、耗时和错误。

代码、数据集、校验器或提示词发生变化后，旧评测会因为指纹不一致自动失效，必须重新运行。

## 只重新评分

```powershell
python -m tests.run_grounded_answer_eval `
  --output tests/grounded_answer_eval_v4_final.json `
  --rescore
```

重新评分不会伪装成一次新的在线生成：它只更新评分器版本和重新评分指纹，不会补写缺失的生成指纹。因此正式发布仍要求重新运行完整在线评测。
