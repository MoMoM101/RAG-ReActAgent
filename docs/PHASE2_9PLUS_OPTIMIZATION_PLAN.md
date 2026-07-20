# RAG Agent Phase 2：9+ 与生产级核心优化执行方案

> 初版日期：2026-07-11  
> 综合更新：2026-07-13  
> 当前版本：`master@24469a8`  
> 当前综合评分：约 **9.0/10**  
> 当前定位：Agent/RAG 已达到单机、单租户、中小流量生产可用；尚未达到多租户、高可用、可水平扩容的企业生产级。  
> 目标：修正关键词检索评测失真，补齐 Agent/RAG 强制超时、检索降级、索引事务、真实可观测性和企业部署能力，形成 **9.2+ 发布候选版本**。

### 2026-07-13 最新状态

以下为当前已验证状态；第 1～12 节保留此前从 8.4 分演进到 9.0 分的过程和门禁，新的未完成工作统一收敛到第 13 节以后。

| 检查项 | 当前结果 | 状态 |
|---|---:|:---:|
| 后端测试 | 419 passed / 4 skipped / 0 failed | ✅ |
| Ruff | All checks passed | ✅ |
| Mypy | 134 个源码文件无问题 | ✅ |
| 前端测试 | 41/41 通过 | ✅ |
| 前端 lint/build | 全绿、零 warning | ✅ |
| Qrels v2 | 主指标合法；keyword-only 评测存在元数据缺失 | ⚠️ |
| BM25 实际索引 | 142 chunks / 7450 postings / 2118 terms，可返回关键词结果 | ✅ |
| Docker Compose 配置 | 校验通过，真实端到端运行仍需留证 | ⚠️ |
| Agent/RAG 生产定位 | 单机生产可用，企业级能力待补 | ⚠️ |

---

## 1. 当前基线

本轮复测结果：

| 检查项 | 当前结果 | 状态 |
|---|---:|:---:|
| 后端测试 | 383 passed / 3 failed / 12 skipped | ⚠️ |
| Ruff | All checks passed | ✅ |
| Mypy | 133 个源码文件无错误 | ✅ |
| 认证专项测试 | 16 passed | ✅ |
| 备份安全测试 | 12 passed | ✅ |
| 评测指标测试 | 34 passed | ✅ |
| 最小依赖启动 | OCR/Rerank/OpenCV 不存在时可启动 | ✅ |
| 前端测试 | 20 passed | ✅ |
| 前端构建 | 构建成功，存在 CSS 警告 | ⚠️ |
| Docker Compose | 配置校验通过，未完成真实启动验证 | ⚠️ |
| Git 交付状态 | 优化在 worktree，尚未合并 `master` | ⚠️ |

当前 3 个后端失败均来自启用 `ADMIN_API_TOKEN` 后，旧 E2E 请求没有携带 `X-Admin-Token`。这说明认证逻辑已经生效，但测试基线尚未完全适配生产安全模式。

---

## 2. 优化原则

1. **可选模型不能成为启动前置条件**：OCR、Rerank 未部署时继续自动降级，健康接口要明确报告组件状态。
2. **高风险路径先补测试再重构**：尤其是备份恢复、索引切换和回滚。
3. **所有阶段设置硬门禁**：当前阶段未通过，不进入下一阶段。
4. **主分支才是最终交付对象**：worktree 通过不等于项目已经完成优化。
5. **指标必须可复现**：评测数据、配置、模型版本和运行结果必须一起留档。

---

## 3. 总体流程

```text
Phase 0  固化当前基线
   ↓ Gate 0：测试结果、提交、配置均可追溯
Phase 1  修复认证 E2E 闭环
   ↓ Gate 1：安全模式与降级模式测试全绿
Phase 2  完成恢复事务与故障注入验证
   ↓ Gate 2：SQLite/Qdrant/BM25/uploads 可一致切换和回滚
Phase 3  正式迁移 Qrels v2 评测
   ↓ Gate 3：新入口、新基线、新报告可复现
Phase 4  前端认证与构建收尾
   ↓ Gate 4：认证交互测试全绿、构建零警告
Phase 5  Docker 真实冒烟测试
   ↓ Gate 5：容器内端到端链路通过
Phase 6  合并主分支并发布验收
   ↓ Gate 6：master 全量门禁通过
```

---

## 4. Phase 0：固化基线

### 工作项

- 记录优化分支提交：`f4bf0bf`、`92d9a03`。
- 保存当前全量测试、Ruff、Mypy、前端测试和构建输出。
- 检查 `.env`、令牌、数据库、上传文件、模型权重没有进入 Git。
- 将发布测试环境固定为：
  - `ADMIN_API_TOKEN=evaluation-admin-token`
  - OCR/Rerank 分别测试启用与禁用状态
  - 固定 embedding 模型、维度和 chunk 配置

### Gate 0

- [ ] 工作树无无法解释的未提交修改。
- [ ] 所有基线命令和结果已归档。
- [ ] 测试使用的令牌只存在于测试环境。
- [ ] 可选模型禁用时后端可以启动并完成基础问答。

---

## 5. Phase 1：认证 E2E 闭环

### 5.1 修复现有失败

在 `backend/tests/test_e2e_mvp.py` 中统一通过 fixture 注入测试令牌，避免每个用例手写请求头：

```python
@pytest.fixture
def admin_headers() -> dict[str, str]:
    return {"X-Admin-Token": "evaluation-admin-token"}
```

修复以下用例：

- `test_list_documents_empty`
- `test_create_and_delete_conversation`
- `test_invalid_file_type`

### 5.2 建立双模式矩阵

认证必须至少验证两种模式：

| 模式 | 环境 | 预期 |
|---|---|---|
| 安全模式 | 设置 `ADMIN_API_TOKEN` | 无令牌 401，错误令牌 401，正确令牌正常 |
| 本地兼容模式 | 未设置固定令牌或使用项目约定的开发配置 | 行为符合配置说明，不产生不可见随机状态 |

同时覆盖普通 HTTP、文件上传和 SSE 聊天。日志不得打印完整令牌。

### Gate 1

- [ ] 当前 3 个 E2E 失败全部修复。
- [ ] HTTP、上传、SSE 均有正确令牌/错误令牌/无令牌测试。
- [ ] 后端全量测试零失败。
- [ ] OCR/Rerank 降级测试仍然通过。

---

## 6. Phase 2：恢复事务与故障注入

这是本阶段最高优先级，也是从 8.4 分提升到 9 分的关键。

### 6.1 完整恢复集成测试

新增针对恢复端点的真实集成测试，至少覆盖：

1. 正常备份后恢复，文档 ID、会话和文件保持一致。
2. 临时 SQLite 构建成功、Qdrant 构建失败。
3. Qdrant 构建成功、切换指针失败。
4. SQLite 已切换、uploads 切换失败。
5. 回滚过程中再次发生异常。
6. 备份来自不同 chunk 配置或 embedding 维度。
7. 恢复包存在丢失文件、篡改哈希、重复 ID 或超限内容。

测试中通过 monkeypatch/fake adapter 在明确的切换点注入异常，并断言：

- 旧数据库仍可访问；
- 旧 Qdrant 指针已恢复；
- 原 uploads 完整；
- 临时目录和临时集合得到清理；
- API 返回可诊断但不泄露内部路径的信息。

### 6.2 补齐一致性清单

恢复提交前必须同时验证：

| 对象 | 必检内容 |
|---|---|
| SQLite | document ID、状态、chunk_count、BM25 行数 |
| Qdrant | collection、向量维度、document ID、chunk ID、point 数量 |
| BM25 | document/chunk ID 集合与 SQLite 一致 |
| uploads | 相对路径、文件大小、SHA-256 |
| 配置 | chunk_size、chunk_overlap、embedding 模型及维度 |

不要只比较数量；数量相同但 ID 不同仍应判定失败。

### 6.3 真正原子化 uploads 切换

- 在与正式 uploads 相同卷上准备完整的新目录。
- 完成文件哈希校验后再执行目录重命名。
- 不在正式路径上执行长时间 `copytree`。
- 保留旧目录至整个事务提交完成，再按保留策略清理。

### 6.4 临时资源回收

- 失败后删除本次创建的临时 Qdrant collection。
- 成功后按保留窗口清理旧 collection，而非永久堆积。
- 清理失败记录 warning 和结构化指标，但不得掩盖主恢复结果。

### 6.5 兼容性策略

备份 manifest 增加：

- schema version；
- chunk_size/chunk_overlap；
- splitter version；
- embedding provider/model/dimension；
- BM25 schema/version；
- 创建时间和项目版本。

不兼容时只能选择“拒绝恢复”或“显式全量重建”，不能静默使用当前配置重切分。

### Gate 2

- [ ] 正常恢复集成测试通过。
- [ ] 至少 5 个切换点的故障注入测试通过。
- [ ] SQLite/Qdrant/BM25/uploads 集合级一致性校验通过。
- [ ] 文件哈希与 embedding 兼容性校验通过。
- [ ] 失败恢复不改变线上可见状态。
- [ ] 临时集合、目录和旧资源有明确清理策略。

---

## 7. Phase 3：Qrels v2 正式接管评测

### 工作项

1. 将 `backend/tests/evaluate_rag.py` 的正式入口切换到 v2 指标实现。
2. 旧 `compute_metrics()` 标记为 legacy，只保留历史结果读取能力，禁止生成新基线。
3. 为每次结果记录：Git commit、语料版本、qrels 版本、chunk 配置、embedding、reranker、OCR 状态和随机种子。
4. 分别运行以下矩阵：

| 场景 | OCR | Rerank | 用途 |
|---|:---:|:---:|---|
| 最小模式 | Off | Off | 验证兜底质量与可运行性 |
| 仅 Rerank | Off | On | 衡量排序收益 |
| 仅 OCR | On | Off | 衡量扫描文档收益 |
| 完整模式 | On | On | 生产上限基线 |

5. 输出 Recall@K、MRR、NDCG@K、答案正确性、拒答准确率、P50/P95 延迟及失败率。
6. 在 README 中只展示 v2 的当前基线，并清楚标记测试环境。

### 推荐发布门槛

具体数值以当前真实基线为准，至少要求：

- 新版本关键指标不低于基线容差；
- 完整模式相对最小模式的收益可量化；
- OCR/Rerank 不可用时失败率不增加；
- 没有相关证据的问题能够稳定拒答，而不是编造答案。

### Gate 3

- [ ] 正式入口不再调用旧指标生成新结果。
- [ ] 四种模型组合都有真实报告。
- [ ] 报告包含完整配置和 commit，可重复运行。
- [ ] README 指标与生成文件一致。

---

## 8. Phase 4：前端收尾

### 认证专项测试

为 `TokenGate`、`authStore` 和 API 拦截器增加测试：

- 初次访问要求输入令牌；
- 正确令牌进入应用；
- 普通请求和 SSE 都携带令牌；
- 401 后清理失效令牌并返回 TokenGate；
- 刷新页面后的持久化行为符合设计；
- UI 和日志不展示完整令牌。

### 清理警告

- 将 `ConfirmDialog.tsx` 中非组件导出移动到独立模块，消除 Fast Refresh 警告。
- 校准 Tailwind/Vite/LightningCSS 配置，消除未知 `@theme`、`@tailwind` 警告。
- 设置 CI：lint warning 视为失败，防止警告重新累积。

### Gate 4

- [ ] 前端认证链路测试覆盖 HTTP 和 SSE。
- [ ] `npm test` 全绿。
- [ ] `npm run lint` 零 warning。
- [ ] `npm run build` 零 warning。

---

## 9. Phase 5：Docker 真实冒烟测试

不能只运行 `docker compose config`，需要真实启动容器并验证：

1. backend、frontend 健康检查通过；
2. 前端代理能够访问 backend；
3. 无令牌请求返回 401；
4. 正确令牌可列出文档、创建会话并完成一次 SSE 请求；
5. 上传一个小型测试文档并完成检索；
6. 禁用 OCR/Rerank 后重复基础链路；
7. 重启容器后数据仍然存在；
8. 宿主机只暴露设计中的端口。

建议把上述检查写成 `.github/workflows` 中的 compose-smoke job，并保存失败时的容器日志。

### Gate 5

- [ ] `docker compose up` 后所有健康检查通过。
- [ ] 前端代理、认证、上传、检索、SSE 链路通过。
- [ ] 可选模型禁用时容器仍可用。
- [ ] CI 中 compose smoke 可重复通过。

---

## 10. Phase 6：合并与发布验收

### 合并前

- 先保护根目录现有未提交修改，禁止直接覆盖。
- 审查根目录与 worktree 在 `backend/main.py`、`backend/api/chat.py`、测试配置等重叠文件上的差异。
- 使用正常 Git 合并流程处理冲突，不用整目录复制替代合并。

### 合并后完整门禁

在 `master` 上重新执行：

```bash
ruff check backend --config pyproject.toml
mypy backend --config-file pyproject.toml
pytest backend/tests
cd frontend && npm test && npm run lint && npm run build
docker compose config --quiet
docker compose up -d --build
```

随后执行认证、上传、检索、SSE 和恢复冒烟测试。所有报告必须来自合并后的 `master` commit，而不是旧 worktree。

### Gate 6 / 发布定义

- [ ] `master` 工作区干净。
- [ ] 后端、前端、静态检查全部零失败、零 warning。
- [ ] Docker 实际冒烟测试通过。
- [ ] 恢复故障注入测试通过。
- [ ] Qrels v2 报告已生成并归档。
- [ ] OCR/Rerank 启用和禁用两种模式均通过。
- [ ] README、配置说明和故障排查文档与实际行为一致。

---

## 11. 推荐实施顺序与工作量

| 优先级 | 工作项 | 预计工作量 | 预期收益 |
|:---:|---|:---:|---|
| P0 | 修复认证 E2E 3 个失败 | 0.5 天 | 全量测试恢复可信 |
| P0 | 恢复集成与故障注入测试 | 1.5～2 天 | 消除最大数据安全风险 |
| P0 | uploads 原子切换、临时资源清理 | 1 天 | 提升恢复可靠性 |
| P1 | BM25/哈希/ID/模型兼容性校验 | 1 天 | 保证多存储一致性 |
| P1 | Qrels v2 正式迁移与实跑 | 1 天 | 评测结果可相信、可比较 |
| P1 | Docker 真实冒烟 CI | 0.5～1 天 | 提升部署可信度 |
| P2 | 前端认证测试与 warning 清零 | 0.5 天 | 改善回归质量 |
| P0 | 合并 `master` 后全量复测 | 0.5 天 | 形成真正可交付版本 |

总体预计：**5～7 个开发日**。如果恢复链路已有可复用的 fake Qdrant/SQLite fixture，可压缩到约 4～5 日。

---

## 12. 目标评分

完成不同阶段后的合理预期：

| 完成范围 | 预期评分 |
|---|:---:|
| 仅修复 3 个 E2E | 8.5 |
| 加上恢复集成与故障注入 | 8.8～8.9 |
| 加上 Qrels v2 与 Docker 实跑 | 9.0～9.1 |
| 合并 master、全量零失败零 warning | **9.1～9.2** |

达到 9 分的核心不是继续增加功能，而是用自动化证据证明：认证不会被绕过、恢复不会破坏数据、指标真实可复现、容器可以实际部署、可选模型缺失时仍能稳定运行。

---

## 13. 关键词检索“完全失效”专项诊断

### 13.1 结论

当前 `evaluation_results_v2.json` 中 keyword-only 的 Precision、MRR、Hit@5、NDCG 均为 0，但只读实测证明 BM25 并未完全失效：

- `bm25_docs`：142 个 chunk；
- `bm25_index`：7450 条倒排记录；
- 不同 term：2118；
- “Python 机器学习”和“深度学习”均能返回结果；
- Jieba 对 `Carbonara`、`420 ppm`、`FastAPI Django` 的分词结果正常。

因此当前问题分成两层：

1. **P0：评测元数据丢失**，导致 keyword-only 被统一误判为 0；
2. **P1：BM25 schema 与索引维护仍不完整**，可能影响来源追踪、section 级评测和长期 IDF 稳定性。

### 13.2 P0 根因：Qrels v2 无法识别 BM25 结果

Qrels v2 使用 `document_key + section_key` 判定相关性。语义检索结果携带这两个字段，但 keyword-only 在 `backend/tests/evaluate_rag.py` 中只填写：

```python
RetrievalResult(
    chunk_id=r.chunk_id,
    document_id=r.document_id,
    text=r.text,
    score=r.score,
    source="keyword",
)
```

结果中的 `document_key`、`section_key` 均为空，所以即使 BM25 返回正确内容也无法匹配 qrels。

### 13.3 快速修复

在评测执行前建立 document ID 到稳定键的映射：

```python
document_key_by_id = {
    doc_id: stable_document_key
}
```

转换 keyword 结果时填写：

```python
RetrievalResult(
    chunk_id=r.chunk_id,
    document_id=r.document_id,
    text=r.text,
    score=r.score,
    source="keyword",
    document_key=r.document_key or document_key_by_id.get(r.document_id, ""),
    section_key=r.section_key,
)
```

该修复用于恢复评测可信度，但不能替代存储层修复。

### 13.4 正式修复：统一 BM25 与 Qdrant 元数据

将 `bm25_docs` 扩展为：

```sql
CREATE TABLE bm25_docs (
    chunk_id TEXT PRIMARY KEY,
    document_id TEXT NOT NULL,
    document_key TEXT NOT NULL,
    section_key TEXT NOT NULL,
    chunk_index INTEGER NOT NULL,
    text TEXT NOT NULL,
    token_count INTEGER NOT NULL
);
```

Qdrant 与 BM25 必须使用相同的：

```text
chunk_id
document_id
document_key
section_key
chunk_index
```

`TextSearchResult` 已经定义 `document_key`、`section_key`，BM25 查询需要真正返回这两个字段，而不是保留默认空字符串。

### 13.5 修复 DF 膨胀和旧 posting 残留

当前相同 chunk 重复写入会再次增加 `df`。如果文本内容发生变化，旧 term posting 也可能残留。正式 upsert 应在同一事务中：

1. 查询旧 chunk 的 term 集合；
2. 删除旧 posting；
3. 对旧 term 的 df 扣减并删除 df=0 的记录；
4. 写入新文档和 posting；
5. 对新 term 的 df 增加；
6. 提交事务并清空统计缓存。

不再允许测试以“暂时接受 df inflation”作为通过条件。

### 13.6 固定数据库位置

默认 URL `sqlite+aiosqlite:///./data/rag_agent.db` 依赖当前工作目录。应改成基于后端目录计算的绝对路径，或者要求环境变量提供绝对路径。

目标：

- 从仓库根目录启动；
- 从 `backend` 目录启动；
- 从 Docker `/app` 启动；
- 从 pytest 临时目录启动；

除测试显式隔离外，均指向预期数据库，不产生多个“看起来都正确”的数据目录。

### 13.7 关键词专项测试

新增完整链路测试：

- 中文：`机器学习`；
- 英文：`Carbonara`、`FastAPI`；
- 数字单位：`420 ppm`；
- 错误码：`ERR_40003`；
- SKU：`STM32H743VI`；
- 中英混合：`Python 机器学习`；
- 同义词与未登录词；
- 相同 chunk 重复 upsert；
- 文本变更后旧 term 不再命中；
- keyword result 的稳定键非空；
- keyword-only Qrels v2 Hit@5 必须大于 0。

### Gate 8：关键词链路

- [ ] keyword-only 不再因空稳定键得到全 0。
- [ ] BM25 结果携带 document_key、section_key、chunk_index。
- [ ] 重复 upsert 不增加错误 df。
- [ ] 文本变更不会留下旧 posting。
- [ ] 中文、英文、错误码、SKU 混合测试全部通过。
- [ ] 数据库路径不依赖当前工作目录。
- [ ] 新 Qrels v2 结果重新生成并加入 Git。

---

## 14. Agent 企业生产级加固

当前 Agent 已有 ReAct 循环、上下文裁剪、循环上限、工具并发、指数退避、SSE、历史持久化和降级总结，满足单机生产基础。企业生产级仍需完成以下工作。

### 14.1 强制 Deadline 与取消传播

当前总时间主要在循环边界检查，单次 LLM stream 或工具如果长期不返回，可能越过总时限。建立统一 deadline：

```text
HTTP request deadline
  ├─ intent classification timeout
  ├─ memory interception timeout
  ├─ each LLM stream timeout
  ├─ each tool timeout
  └─ final synthesis timeout
```

建议实现：

```python
deadline = loop.time() + settings.max_total_time

async with asyncio.timeout(max(0.1, deadline - loop.time())):
    await operation()
```

同时区分：

- connect timeout；
- 首 token timeout；
- stream idle timeout；
- 工具单次 timeout；
- 整个 Agent deadline。

客户端断开 SSE 后应取消上游 LLM、并行工具和无必要后台协程，并在 `finally` 中记录取消状态。

### 14.2 LLM/Embedding 客户端策略

显式配置：

- connect/read/write/pool timeout；
- 429、408、5xx 最大重试；
- 带抖动的指数退避；
- Retry-After；
- 并发连接池上限；
- 优雅关闭客户端；
- 供应商级熔断和半开探测。

禁止完全依赖 SDK 默认超时和默认重试。

### 14.3 工具输入与副作用治理

- 使用 JSON Schema/Pydantic 在执行前验证工具参数；
- 未注册工具名返回结构化错误，不抛出 KeyError；
- 每个工具声明 timeout、幂等性、副作用等级和权限；
- 副作用工具增加 idempotency key；
- 高风险工具增加用户确认或策略引擎；
- 工具错误只向用户返回安全摘要，详细栈仅进入日志；
- 并行执行时单个工具失败不得破坏其他只读工具结果。

### 14.4 Prompt Injection 与数据泄漏防护

检索文档属于不可信数据，必须与系统指令明确隔离：

- 用结构化容器标记 `UNTRUSTED_RETRIEVED_CONTENT`；
- 文档中的“忽略系统指令”等内容不能成为 Agent 指令；
- 工具权限不由检索文本决定；
- 敏感数据输出增加策略检查；
- 搜索外部网页时过滤脚本、隐藏内容和恶意指令；
- 增加注入攻击测试集和红队用例。

### 14.5 回答事实与引用校验

最终回答后增加轻量 verifier：

1. 提取回答中的主要事实；
2. 将事实映射到 source chunk；
3. 标记无证据事实；
4. 引用不支持结论时重新生成或降级拒答；
5. 返回引用覆盖率和 verifier 状态。

评测增加：

- answer factuality；
- citation correctness；
- citation completeness；
- refusal accuracy；
- hallucination rate。

### 14.6 持久化任务队列

当前 `asyncio.create_task()` 适合单实例，但进程重启会丢任务。企业部署改用持久化队列：

- PostgreSQL 任务表 + worker，或 Celery/ARQ/Dramatiq；
- 任务租约和心跳；
- 最大重试与死信队列；
- 幂等 ingestion；
- 可取消任务；
- 部署时优雅排空；
- worker 独立扩容。

### Gate 9：Agent 生产级

- [ ] 所有 LLM、工具调用受统一 deadline 控制。
- [ ] SSE 断开能够取消上游任务。
- [ ] 工具参数执行前验证，副作用工具支持幂等。
- [ ] Prompt Injection 测试不会触发越权工具或泄漏。
- [ ] 回答事实和引用有自动校验。
- [ ] ingestion/后台任务重启后可恢复。
- [ ] 429、5xx、超时、断流故障注入测试通过。

---

## 15. RAG 企业生产级加固

### 15.1 混合检索单路故障降级

当前 semantic 与 BM25 使用 `asyncio.gather()`，任一路抛异常可能使整个检索失败。目标策略：

| 故障 | 降级行为 |
|---|---|
| Qdrant 失败 | 使用 BM25 返回，标记 `keyword_only_fallback` |
| BM25 失败 | 使用向量结果，标记 `semantic_only_fallback` |
| Rerank 失败 | 使用 RRF 原始顺序 |
| Query rewrite 失败 | 使用原始 query |
| Embedding 失败 | 若业务允许，尝试 keyword-only |
| OCR 失败 | 保存可提取文本并报告 degraded |

降级不能静默发生。响应元数据、日志和指标必须记录实际使用的检索路径。

### 15.2 索引写入的 generation 事务

当前 Qdrant → BM25 → SQLite ready 属于跨存储双写，进程在中间崩溃可能留下部分索引。引入 `generation_id`：

```text
1. SQLite 创建 generation=G，状态 staging
2. Qdrant 写入 G
3. BM25 写入 G
4. 比较两边 chunk ID 集合
5. SQLite 将 G 标记 committed
6. 检索只读取 committed generation
7. 异步清理旧 generation
```

这样普通异常和进程崩溃都不会让半成品对查询可见。

### 15.3 检索质量与容量门禁

正式测试集从 31 条回归查询扩展到至少 200 条真实或人工审核查询，覆盖：

- 精确关键词；
- 自然问题；
- 跨文档问题；
- 无答案问题；
- 冲突文档与版本更新；
- 表格、扫描 PDF、多栏 PDF；
- 中文、英文、中英混合；
- Prompt Injection 文档；
- 超长文档和大批量文档。

建立不同规模基线：

```text
1k chunks
10k chunks
100k chunks
1m chunks（目标架构验证）
```

记录 Recall、MRR、NDCG、Hit Rate、P50/P95/P99、吞吐、错误率、内存和成本。

### 15.4 Rerank 与 OCR 实际状态

配置开关不等于模型已生效。报告应记录：

```json
{
  "rerank_requested": true,
  "rerank_loaded": true,
  "rerank_fallback_reason": null,
  "queries_with_order_change": 0,
  "ocr_requested": true,
  "ocr_loaded": true,
  "ocr_degraded_pages": 0
}
```

必须完成四模式评测：

- OCR Off / Rerank Off；
- OCR On / Rerank Off；
- OCR Off / Rerank On；
- OCR On / Rerank On。

如果 Rerank 与 no-rerank 指标相同，需要证明模型是否加载、排序是否变化，而不是只报告 `enabled=true`。

### 15.5 多实例存储架构

单机部署可继续使用 SQLite + 本地 Qdrant。需要水平扩容时迁移为：

- PostgreSQL：文档、会话、状态和任务；
- 独立 Qdrant Server/Cluster：向量索引；
- Redis：分布式限流、短期缓存和锁；
- S3/MinIO：上传文件和备份；
- 持久化 worker queue：OCR、embedding 和索引任务；
- tenant namespace/collection partition：租户隔离。

禁止多个进程同时直接打开同一个本地 Qdrant 目录。

### Gate 10：RAG 生产级

- [ ] Qdrant/BM25 任一路失败时仍能返回另一侧结果。
- [ ] 降级路径对用户和监控可见。
- [ ] 跨存储写入使用 committed generation。
- [ ] 进程在每个写入阶段崩溃都不会暴露半成品。
- [ ] keyword-only、semantic-only、hybrid、rerank 指标均真实有效。
- [ ] 真实业务查询集达到至少 200 条。
- [ ] 有并发、容量、P95/P99 和成本报告。
- [ ] 多实例部署不共享本地 SQLite/Qdrant 文件。

---

## 16. 可观测性闭环

当前 `metrics.py` 已定义 Agent、Tool、LLM、Embedding、ingestion 指标结构，但业务代码几乎没有调用对应的 record 方法，指标接口可能长期返回零。需要真正接通：

### 必接指标

- HTTP 请求量、错误率、P50/P95/P99；
- Agent 循环次数、总耗时、超时和 loop limit；
- LLM 首 token、总耗时、token 数、错误码和供应商；
- 工具调用量、成功率、重试率、P95；
- semantic/BM25/rewrite/rerank 各阶段延迟；
- 每路候选数、融合数、最终结果数；
- 检索降级次数和原因；
- ingestion 队列长度、耗时、失败和重试；
- OCR/Rerank loaded/degraded/error；
- Qdrant collection 数量、清理失败；
- 估算调用成本。

### 链路追踪

`request_id` 应传播到：

```text
HTTP → Agent run → LLM request → tool call → retrieval → Qdrant/BM25 → response
```

使用 Prometheus/OpenTelemetry 输出，而不是只保存在进程内最近 1000 个样本。定义告警：

- Agent timeout > 1%；
- 检索错误率 > 1%；
- P95 超过 SLO；
- ingestion backlog 持续增长；
- Qdrant/BM25 降级持续出现；
- OCR/Rerank 长时间不可用。

### Gate 11：可观测性

- [ ] metrics record 方法均有真实调用点和测试。
- [ ] 单次请求可以通过 request_id 追踪完整链路。
- [ ] Prometheus/OpenTelemetry 能抓取真实非零指标。
- [ ] 关键故障有告警和运行手册。

---

## 17. 安全、租户与权限

管理员令牌适合单用户部署，多租户生产需增加：

- 用户身份认证；
- tenant_id/user_id；
- 文档 ACL；
- 会话、记忆和检索按租户过滤；
- Qdrant payload tenant filter；
- BM25 tenant_id 索引；
- 上传和备份按租户 namespace；
- 审计日志；
- Secret 管理和轮换；
- 数据删除与保留策略。

所有检索入口都必须在数据库、Qdrant 和 BM25 三层同时应用租户过滤，不能只在 API 返回前过滤。

### Gate 12：安全隔离

- [ ] 不同租户无法检索、引用或推断彼此文档。
- [ ] 会话与记忆按用户隔离。
- [ ] 工具权限由服务端策略决定。
- [ ] 管理操作有审计记录。
- [ ] 越权和侧信道测试通过。

---

## 18. 综合实施优先级

| 优先级 | 工作项 | 预计工作量 | 目标 |
|:---:|---|:---:|---|
| P0 | 修复 keyword-only 稳定键与重新评测 | 0.5 天 | 恢复评测可信度 |
| P0 | BM25 schema 元数据 + DF 正确 upsert | 1 天 | 关键词索引长期可靠 |
| P0 | LLM/Tool/RAG 强制 deadline | 1 天 | 防止请求无限挂起 |
| P0 | semantic/BM25 单路故障降级 | 0.5～1 天 | 提升在线可用性 |
| P1 | 接通真实 metrics 与 request_id 链路 | 1 天 | 可发现、可定位故障 |
| P1 | 回答事实与引用校验 | 1～2 天 | 降低幻觉和错误引用 |
| P1 | generation 索引事务 | 2 天 | 解决跨存储半成品 |
| P1 | 持久化任务队列 | 1.5～3 天 | 支持重启恢复和 worker 扩容 |
| P1 | 200+ 查询质量与容量评测 | 1～2 天 | 建立真实生产基线 |
| P2 | Prompt Injection 红队与策略 | 1～2 天 | 提升不可信文档安全性 |
| P2 | 多租户 ACL | 3～5 天 | 支持 SaaS/多部门隔离 |
| P2 | PostgreSQL/Qdrant Server/对象存储 | 3～7 天 | 支持水平扩容和高可用 |

### 最短发布路线

如果目标仍是单机、单租户部署，完成以下五项即可形成更稳健的 9.2 发布候选：

1. 修复 BM25 稳定键和 keyword-only 评测；
2. 修复 DF/upsert；
3. 增加强制 deadline；
4. 增加双路检索降级；
5. 接通真实指标并完成 Docker 端到端冒烟。

如果目标是企业多租户生产，还必须继续完成 generation 事务、持久化队列、外部存储、多租户 ACL、Prompt Injection 防护和容量压测。

---

## 19. 2026-07-13 本轮执行记录

### 已执行优化

- 后台任务入口统一改为协程工厂，避免任务在调度前被取消时产生 `coroutine was never awaited`。
- `task_queue` 状态写入改为严格的 `pending -> running -> done/failed` 顺序，消除异步插入与更新竞态。
- 为运行中的后台任务增加周期 heartbeat；任务完成、失败或取消时同步终止 heartbeat。
- 明确启动恢复语义：当前只把超时任务标记为失败，不宣称自动重放任务。
- semantic 与 BM25 多查询改为独立故障域；任一路失败时保留另一条路径的结果，并写入 `fallback_reason`。
- embedding、向量库、BM25 的创建失败也纳入降级判定；两路均失败时抛出 `RetrievalError`，不再静默返回空结果。
- 清理项目现有 Ruff 导入排序问题，并增加后台任务和检索降级专项测试。

### 实测验收结果

| 门禁 | 结果 |
|---|---|
| 后台任务 + RAG 专项测试 | `20 passed` |
| 全量测试 | `433 passed, 4 skipped` |
| Ruff | `All checks passed` |
| Mypy | `Success: no issues found in 136 source files` |
| 未等待协程 RuntimeWarning | 本轮全量测试未再出现 |

### 本轮未冒充完成的事项

- 持久化任务目前具备状态记录、heartbeat 和陈旧任务识别，但尚不具备跨进程自动重放能力。
- generation 表尚未形成“检索只读取 committed generation”的原子可见性闭环；Qdrant/BM25 跨存储写入仍需后续完善。
- `llm_first_token_timeout` 仍需接入真实流式首 token 超时控制。
- keyword-only 代码与专项测试已经恢复，但正式质量分数必须使用固定语料、固定 qrels 和真实模型重新评测，不能用单元测试结果替代。
- Docker 端到端、OCR/Rerank 四组合、200+ 真实查询集、并发 P95/P99 和故障注入尚需独立验收。

### 当前建议

本轮可视为 P0 稳定性修复完成。单机、单租户发布候选可进入真实数据评测和 Docker 冒烟阶段；在 generation 原子可见性、任务自动重放和容量报告完成前，不应宣称为企业级多实例生产版本。

---

## 20. 最终验收矩阵

| 层级 | 验收标准 | 当前 |
|---|---|:---:|
| 单机功能生产 | 测试全绿、认证、备份恢复、OCR/Rerank 降级 | ✅ |
| 单机可靠生产 | Deadline、双路降级、真实 metrics、Docker 实跑 | ⚠️ |
| 检索质量生产 | keyword/semantic/hybrid/rerank 指标可信，200+ 查询 | ⚠️ |
| 数据一致性生产 | generation 提交、崩溃恢复、chunk ID 集合一致 | ⚠️ |
| 企业安全生产 | 多租户 ACL、审计、注入防护、Secret 轮换 | ❌ |
| 分布式高可用 | 外部数据库、Qdrant 集群、队列、对象存储、压测 | ❌ |

最终结论应明确写为：

> 当前版本适合单机、单租户、内网或中小流量生产部署。完成 Gate 8～11 后可称为稳健单机生产级；完成 Gate 12 和分布式存储迁移后，才可称为企业级多租户生产系统。
