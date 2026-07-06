# 评审报告优化计划

**日期**: 2026-07-01 | **来源**: 项目评审报告.docx | **版本**: Phase 1-4 汇总

---

## 评审结论摘要

项目整体完成度高，架构分层清晰，Agent 循环/意图分类/记忆系统/检索管线/前端交互均达到可用水平。主要短板集中在三方面：**测试覆盖**、**LLM 调用效率**、**资源管理**。

---

## 问题分级

### 高优先级

| # | 问题 | 影响 | 涉及文件 |
|---|------|------|----------|
| 1 | Agent Loop 等关键路径零测试覆盖 | 任何改动都可能引入回归 bug，无法自动化验证 | loop.py, classifier.py, intercept.py, context.py |
| 2 | 单次用户消息最多 5 次 LLM 调用 | 延迟高（每次~1-3s）、API 费用高、免费额度快速耗尽 | loop.py, classifier.py, intercept.py |
| 3 | UserProfile INSERT 追加无限膨胀 | 用户每说一次"我叫XX"就新增一条~1KB记录，数据库持续增长 | profile.py |
| 4 | Embedding/Qdrant 每次操作重建实例 | 连接/模型反复加载，资源浪费 | embedding/, vectordb/ |

### 中优先级

| # | 问题 | 涉及文件 |
|---|------|----------|
| 5 | Chunk 质量评分对代码/API 文档过于苛刻 | retriever.py |
| 6 | Bing HTML 选择器脆弱、输入限制无提示、session_extract 硬截断 | tools.py, chat.py, session_extract.py |

### 低优先级

| # | 问题 |
|---|------|
| 7 | 后台任务监控缺位、trim_messages 用 id() 去重、无 rate limiting、无 metrics |

---

## 分期实施

### Phase 1 — 核心稳定性（本次）

1. **UserProfile 存储优化** → [设计文档](../specs/2026-07-01-phase1-userprofile-storage.md)
2. **LLM 调用次数削减** → [设计文档](../specs/2026-07-01-phase1-llm-call-reduction.md)
3. **Embedding/Qdrant 实例复用** → [设计文档](../specs/2026-07-01-phase1-instance-reuse.md)

### Phase 2 — 测试与健壮性

1. Agent Loop 核心测试（Mock LLM 注入）
2. 意图分类 8 种规则边界测试
3. 记忆拦截正则/LLM 分支测试
4. Bing 搜索容错 + 日志
5. 前端输入长度限制
6. session_extract 按消息数截断
7. Chunk 质量评分开关

### Phase 3 — 锦上添花

1. Rate limiting (slowapi)
2. Prometheus metrics 端点
3. trim_messages 内容 hash 去重
4. 后台 ingestion 监控

---

## 实施原则

- 每期改动独立可上线，不依赖后续分期
- 纯后端改动优先，减少前端耦合
- 改动范围最小化，不引入不必要的重构
- 每项完成后运行现有测试确保无回归
