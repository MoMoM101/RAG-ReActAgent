# RAG Agent 待优化模块

> 更新日期：2026-07-08
> 已完成优化：FTS5 trigram 迁移、Qdrant 客户端健壮化、评测脚本重构、chunk splitter 优化

---

## 一、搜索质量

### 1.1 重新跑评测验证 chunk_size=200 效果

**现状**: chunk_size 从 384 降到 200，splitter 加了段落优先 + 表格保护，但还没跑过完整评测。

**操作**: `cd backend && python tests/evaluate_rag.py`

**预期**: P@5 应从 24.5% 提升到 35-50%，hybrid source 分析中 keyword 贡献应增加（chunk 更多、关键词匹配更精确）。

---

## 二、测试盲区

### 2.1 Context overflow 降级路径

**位置**: `agent/loop.py:126-133`

```python
if is_context_error(e) and context_window // 2 >= _MIN_CONTEXT_WINDOW:
    context_window //= 2
    ctx_manager = ContextManager(max_tokens=context_window)
```

**缺失**: 上下文溢出时窗口折半重试的 2 条路径完全无测试：
- 折半后成功 → 继续正常流程
- 折半到 `_MIN_CONTEXT_WINDOW` 仍失败 → 返回 CONTEXT_ERROR

**方案**: 用 FakeLLM 依次抛出 fake context error 再返回正常响应，验证降级 + 恢复逻辑。

### 2.2 网络搜索 HTTP mock 测试

**位置**: `agent/tools.py:424-470 (_search_bing)`, `agent/tools.py:473-526 (_search_ddgs)`

**缺失**:
- Bing 返回 200 + 正常 HTML 解析
- Bing 返回非 200
- Bing 超时 → fallback DDG
- DDG 全失败 → 最终错误信息
- 两个搜索引擎同时失败 → 折叠错误信息

**方案**: 用 `responses` 或 `pytest-httpx` mock HTTP 调用。

### 2.3 Session extract 记忆提取

**位置**: `agent/session_extract.py:7-67 (extract_session_memories)`

**缺失**:
- 上次提取后新增 >= 5 条消息 → 触发提取
- 不足 5 条 → 跳过
- LLM 提取返回 `[]` → 不更新 `last_extracted_at`
- LLM 返回有效结果 → 更新 `last_extracted_at` + 写入画像

**方案**: FakeLLM 注入 `_extract_with_llm` 的调用。

### 2.4 `_process_dropped` 后台记忆提取

**位置**: `agent/loop.py:261-293`

**缺失**: 上下文裁剪时丢弃的用户消息被后台提取画像信息。验证正则 + LLM 双重提取、批量确认、写入画像的完整链路。

**方案**: FakeLLM 注入 + 验证 `handle_intercept` 被调用。

### 2.5 重试逻辑 `_is_retryable_exception`

**位置**: `agent/tools.py:23-54`

**缺失**: 6 种异常类型（asyncio.TimeoutError, ConnectionError, sqlalchemy Operational/Interface/Timeout/DBAPI, httpx 系列, qdrant/grpc 模块异常）的分类正确性无单元测试。

**方案**: 每种类型构造真实的异常实例，验证 `_is_retryable_exception()` 返回预期值。

---

## 三、代码质量（技术债）

### 3.1 ruff 25 条预存错误

分布：

| 文件 | 数量 | 类型 |
|------|:---:|------|
| `agent/tools.py` | 3 | DDGS 变量命名、return 简化 |
| `agent/classifier.py` | 1 | 嵌套 if 合并 |
| `api/chat.py` | 2 | import 排序、suppress |
| `api/documents.py` | 3 | import 排序 ×3 |
| `api/settings.py` | 3 | V2_SUFFIX 命名、zip strict、PS 别名 |
| `main.py` | 1 | import 排序 |
| `memory/profile.py` | 2 | zip strict ×2 |
| `models/orm.py` | 1 | StrEnum 继承 |
| `ocr/preprocess.py` | 1 | 三元运算符 |
| `rag/pipeline.py` | 2 | import 排序、zip strict |
| `rag/progress.py` | 1 | suppress |
| `reranker/cross_encoder.py` | 1 | for 循环简化 |
| `storage/files.py` | 1 | suppress |
| `tests/` 3 个文件 | 3 | suppress、return 简化、nested with |

**操作**: `ruff check --fix` 可自动修 6 条，剩余的需手动或 `--unsafe-fixes`。

### 3.2 Qdrant deprecated API

**位置**: `vectordb/qdrant.py:128-131`

```python
# 当前 (deprecated)
results = await asyncio.to_thread(
    self.client.search, collection_name=self.collection,
    query_vector=vector, limit=top_k)

# 应改为
results = await asyncio.to_thread(
    self.client.query_points, collection_name=self.collection,
    query=vector, limit=top_k)
```

**影响**: Qdrant 1.13 已标记 `search` 为 deprecated，未来版本可能移除。运行时正常，但有 DeprecationWarning。

### 3.3 Settings rebuild 流程硬编码

**位置**: `api/settings.py:540-730`

`V2_SUFFIX = "_v2"` 变量名和 `_v2` 硬编码散落在多处（建表、写入、切换）。逻辑正确但可维护性差。

**方案**: 抽出 `_rebuild_collection(name, docs)` 独立方法，"双缓冲"模式的标准实现复用。

---

## 四、功能增强

### 4.1 Chunk quality filter 改进

**位置**: `rag/retriever.py:107-141 (_chunk_quality_score)`

**现状**: 用正则判断符号密度、短行比例、重复字符。能拦截纯 TOC/分隔线/页码，但无法判断语义质量。

**方案**: 对 quality_score=1.0 的 chunk 不做处理，对 score < 1.0 的候选 chunk 用轻量 LLM 判分（一次调用判多个）。

### 4.2 用户画像记忆过期

**位置**: `memory/profile.py:14 (MAX_FACTS = 30)`

**现状**: 画像最多 30 条 facts，满了直接截断（保留最后 30 条）。无基于时间或重要性的淘汰。

**方案**: 给每条 fact 加权（最近访问次数 + 创建时间衰减），满 30 条时淘汰最低权重的。

### 4.3 用户认证

**现状**: 无登录机制，单用户。CORS 仅允许 localhost:5173。

**方案**: JWT + bcrypt 密码登录，或在反向代理（nginx/Caddy）层加 basic auth。数据库已有 user_profiles 表结构，扩展即可。

---

## 五、前端

### 5.1 前端测试

**现状**: 0 个前端测试。

**方案**: Vitest + React Testing Library 覆盖核心组件（ChatInput、MessageBubble、DocumentList、UploadZone）。优先测聊天输入和文件上传交互。

### 5.2 Toast 管理

**现状**: `toastStore.ts` 基于 zustand，手动管理 toast 栈。无自动消失、无类型区分。

**方案**: 增加 `type: success|error|info|warning`、`duration` 自动关闭、`maxToasts` 上限。

---

## 六、优先级建议

| 优先级 | 项目 | 预估 | 类别 |
|:---:|------|:----:|------|
| **P0** | 跑评测验证 chunk_size=200 | 0.5h | 验证 |
| **P1** | Context overflow 降级测试 | 1h | 测试 |
| **P1** | 网络搜索 mock 测试 | 1.5h | 测试 |
| **P1** | Session extract 测试 | 1h | 测试 |
| **P2** | `_process_dropped` 测试 | 0.5h | 测试 |
| **P2** | `_is_retryable_exception` 测试 | 0.5h | 测试 |
| **P2** | ruff 25 条修复 | 1h | 代码质量 |
| **P2** | Qdrant query_points 迁移 | 0.5h | 技术债 |
| **P3** | Chunk quality filter → LLM 判分 | 2h | 搜索质量 |
| **P3** | 用户画像记忆过期 | 2h | 功能增强 |
| **P3** | 前端测试 | 3h | 前端 |
| **P3** | 用户认证 | 4h | 安全 |

---

## 七、本次优化已完成项

| 日期 | 项目 | commits |
|------|------|:---:|
| 07-07 | FTS5 unicode61 → trigram | `83da48b`, `abde231`, `c10ff40` |
| 07-07 | Qdrant 健康感知 + 遗留目录清理 | `3f487c1`, `3836bfa` |
| 07-07 | 评测脚本消融对比 + --compare | `d2208bc` |
| 07-07 | dot 转义、lint/type 修复 | `dd7fb3a`, `813ea56` |
| 07-08 | chunk_size=200, 段落优先, 表格保护 | `fd4e6ef` |

---

## 八、2026-07-08 全量优化完成总结

### P0 — 评测验证
- chunk_size=200 评测：P@5 从 22.6% → 27.7%（**+5.1%**），Hit@5 保持 90%

### P1 — 核心测试补全（+5 tests）
- **Context overflow**：已有 5 个测试覆盖折半降级全路径（`test_context_overflow.py`）
- **网络搜索**：新增 3 个测试覆盖 Bing→DDG fallback、双失败、RetryableError 合并（`test_web_search.py`）
- **Session extract**：新增 2 个测试覆盖成功更新 last_extracted_at、空结果跳过（`test_session_extract.py`）
- **FakeLLM**：已在 `conftest.py` 统一管理，`make_fake_llm` fixture 全局可用

### P2 — 测试收尾 + 代码质量
- **`_process_dropped`**：已有 3 个测试（`test_context_overflow.py`）
- **`_is_retryable_exception`**：已有 10 个测试覆盖 6 种异常类型（`test_tools.py`）
- **ruff**：28 → 5 错误（剩余为 N806/N817 命名偏好，非 bug）
- **Qdrant**：`search` → `query_points` API 迁移完成，所有检索测试通过

### P3 — 功能增强
- **Chunk quality LLM**：新增 `_llm_chunk_quality_batch` 批量判分函数，`chunk_quality_llm_enabled` 配置开关（默认关闭）
- **画像记忆过期**：加权评分 = `access_count×0.3 + recency×0.7`，满 30 条按权重淘汰
- **前端测试**：20 个测试覆盖 ChatInput（9 tests）+ MessageBubble（11 tests），Vitest + RTL

### 测试总数变化
| 指标 | 优化前 | 优化后 |
|------|:---:|:---:|
| 后端测试 | 195 | 200 |
| 前端测试 | 0 | 20 |
| 总计 | 195 | 220 |
