# 项目缺口清单

> 更新日期：2026-07-06

---

## 一、测试覆盖盲区

| # | 测试项 | 状态 | 说明 |
|---|--------|:--:|------|
| 1 | 多工具并行调用 | ✅ 已补 | `test_loop.py::TestAgentLoopParallelTools` |
| 2 | Context overflow 降级路径 | ❌ | `loop.py:126-133`, `window //= 2` → retry 两条路径无测试 |
| 3 | `_process_dropped` 画像提取 | ❌ | `loop.py:248`, 裁剪消息后台记忆提取无测试 |
| 4 | `trim_messages` Token 预算边界 | ❌ | 恰好在预算线上的消息、多 tool pair 原子裁剪、中英文混合预算 |

### LLM 依赖函数（需 FakeLLM 提取到 conftest.py）

| 函数 | 文件:行号 | 缺失内容 |
|------|-----------|----------|
| `_is_retryable_exception()` | `tools.py:23-53` | 6 种异常类型分类无直接单元测试 |
| `_search_bing()` | `tools.py:424-470` | Mock HTTP 后可测超时/成功/非200 |
| `_search_ddgs()` | `tools.py:473-526` | Mock 后可测超时/library missing |
| `_extract_with_llm()` | `session_extract.py:72-139` | 对话记忆提取，依赖 LLM |
| `extract_session_memories()` | `session_extract.py:7-67` | 增量提取 + DB 更新 |
| `build_system_prompt()` 模板加载 | `context.py:33-42` | 模板文件缺失/损坏行为未测 |
| `confirm_memory()` | `intercept.py:84-117` | 单条记忆 LLM 确认 |
| `confirm_candidates_batch()` | `intercept.py:119-173` | 批量确认 |
| `_llm_classify()` | `classifier.py:108-158` | LLM 意图分类 |

---

## 二、安全问题

| # | 问题 | 位置 | 风险 | 建议 |
|---|------|------|------|------|
| 1 | Prompt Injection | `context.py` system prompt | system prompt 无输入转义 | 低优先级，强制约束前缀已降低风险 |

---

## 三、已知架构限制

| # | 限制 | 位置 | 说明 |
|---|------|------|------|
| 1 | 单例模式限制多 Worker | `llm/factory.py`, `embedding/factory.py`, `vectordb/qdrant.py` | 模块级单例，仅支持单 Worker 部署。改为连接池模式需较大重构 |
| 2 | 无生产模式启动 | `main.py` | `python main.py` 走 `npm run dev` 开发模式。Docker Compose 已覆盖生产部署需求 |

---

## 四、建议下一步

1. **补测试盲区**（2-3h）— 提 `FakeLLM` 到 `conftest.py`，补 context overflow 降级 + `_is_retryable_exception` + session_extract 测试
2. **修复真实 reranker 模型加载** — 验证 `bge-reranker-v2-m3` 模型完整性，确保通过 factory 调用时正常加载
