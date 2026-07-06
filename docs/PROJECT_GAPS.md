# 项目缺口清单

> 基于 `AGENT_EVALUATION.md` 测评报告 + P0/P1/P2 优化后的残留项
> 更新日期：2026-07-06

---

## 一、测试覆盖盲区

### 1.1 评估报告指出的缺失（4 项）

| # | 测试项 | 状态 | 说明 |
|---|--------|:--:|------|
| 1 | 多工具并行调用 | ✅ 已补 | `test_loop.py::TestAgentLoopParallelTools`，2 个用例 |
| 2 | Context overflow 降级路径 | ❌ | `loop.py:126-133`，`window //= 2` → retry → 成功/失败 两条路径均无测试 |
| 3 | `_process_dropped` 画像提取 | ❌ | `loop.py:248`，裁剪消息的后台记忆提取无测试 |
| 4 | `trim_messages` Token 预算边界 | ❌ | 仅测了基础行为，极限场景未覆盖（恰好在预算线上的消息、多 tool pair 原子裁剪、中英文混合预算） |

### 1.2 本次优化涉及但未测试的代码

| 函数/方法 | 文件:行号 | 缺失内容 |
|-----------|-----------|----------|
| `_is_retryable_exception()` | `tools.py:23-53` | 6 种异常类型分类逻辑无直接单元测试 |
| `_raise_if_retryable()` | `tools.py:56-59` | 同上 |
| `_search_bing()` | `tools.py:424-470` | Mock HTTP 后可测：超时 → RetryableError、200 → parse、非200 → ToolResult |
| `_search_ddgs()` | `tools.py:473-526` | Mock 后可测：超时 → RetryableError、library missing → ToolResult |
| `_extract_with_llm()` | `session_extract.py:72-139` | 对话记忆提取，依赖 LLM 所以需要 FakeLLM |
| `extract_session_memories()` | `session_extract.py:7-67` | 增量提取 + DB 更新，需要 Mock DB + LLM |
| `build_system_prompt()` 模板加载 | `context.py:33-42` | 模板文件缺失/损坏时的行为未测 |
| `confirm_memory()` | `intercept.py:84-117` | 单条记忆 LLM 确认，依赖 LLM |
| `confirm_candidates_batch()` | `intercept.py:119-173` | 批量确认，依赖 LLM |
| `_llm_classify()` | `classifier.py:108-158` | LLM 意图分类，依赖 LLM |

### 1.3 测试架构建议

当前 `FakeLLM` 只在 `test_loop.py` 中定义，建议提取到 `tests/conftest.py` 作为共享 fixture：

```python
# tests/conftest.py
import pytest
from llm.base import BaseLLM, LLMResponse, ToolCall

class FakeLLM(BaseLLM):
    """可注入预定响应的 LLM"""
    def __init__(self, response_queues: list[list[LLMResponse]]):
        self.queues = response_queues
        self.call_index = 0

    async def chat_stream(self, messages=None, tools=None):
        if self.call_index >= len(self.queues):
            yield LLMResponse(content="[FakeLLM exhausted]")
            return
        queue = self.queues[self.call_index]
        self.call_index += 1
        for resp in queue:
            yield resp

@pytest.fixture
def make_fake_llm():
    """创建 FakeLLM 并注入到 llm.factory"""
    import llm.factory
    llm.factory.reset_llm()
    def _make(queues):
        fake = FakeLLM(queues)
        llm.factory._llm_instance = fake
        return fake
    return _make
```

这样 `test_classifier.py`、`test_intercept.py`、`test_session_extract.py` 都能复用，覆盖那些当前因依赖真实 LLM 而无法测试的逻辑。

---

## 二、安全问题（评估报告已标记）

| # | 问题 | 位置 | 风险 | 建议 |
|---|------|------|------|------|
| 1 | Prompt Injection | `context.py` system prompt | system prompt 无输入转义，用户消息中的特殊标记可能绕过约束 | 低优先级，强制约束前缀（`【以下是你唯一可以使用的回答来源】`）已降低风险 |
| 2 | SQL 字符串拼接 | `api/documents.py:134` | 有正则前置校验（`^[a-zA-Z0-9_-]+$`），实际风险可控 | 非 Agent 范围，但建议改用参数化查询 |

---

## 三、工程基建缺口

| 项目 | 当前状态 | 建议方案 | 工作量 |
|------|----------|----------|--------|
| CI/CD | 无 | GitHub Actions，push 自动跑 pytest | 10 行 YAML |
| Type checker | 无 | mypy 或 pyright，渐进式覆盖 | 配置 + 逐步修类型错误 |
| Linter | 无 | ruff，一把过修格式问题 | 配置 + 自动修复 |
| Docker | 无 | `Dockerfile` + `docker-compose.yml`（frontend + backend + Qdrant） | ~50 行 |
| 测试覆盖率 | 无统计 | `pytest-cov`，CI 中设最低覆盖率阈值 | 配置 |

---

## 四、已完成的优化回顾

### P0 — 核心修复

| # | 项目 | 改动 |
|---|------|------|
| P0-1 | 工具重试死代码 | `tools.py`: 新增 `RetryableError`，6 个工具区分业务错误/基础设施错误，`ToolRegistry` 指数退避重试生效 |
| P0-2 | Token 估算 | `context.py`: `len(text)//2` → `tiktoken.cl100k_base` 精确计数 |

### P1 — 生产级完善

| # | 项目 | 改动 |
|---|------|------|
| P1-1 | 后台任务日志 | `loop.py`: `except Exception: pass` → `logger.warning(..., exc_info=True)` |
| P1-2 | HTTP 异步化 | `tools.py`: `asyncio.to_thread(httpx.get)` → `httpx.AsyncClient` |
| P1-3 | Prompt 模板化 | `context.py` + `prompts/system.txt`: System Prompt 提取为模板文件 |
| P1-4 | 裁剪日志 | `context.py`: `trim_messages` 裁剪时添加 info 日志 |

### P2 — 锦上添花

| # | 项目 | 改动 |
|---|------|------|
| P2-1 | 循环上限区分 | `loop.py`: 新增 `LOOP_LIMIT` error 事件，区分"兜底回复"vs"自动总结" |
| P2-2 | 模块导出 | `__init__.py`: 导出 7 个关键类/函数 |
| P2-3 | 并行工具测试 | `test_loop.py`: 2 个并行 tool_calls 测试用例 |

### Bug 修复

| 问题 | 发现方式 | 修复 |
|------|----------|------|
| `TIME_LIMIT` 被误标为 `LOOP_LIMIT` | 周边链路检查 | `loop.py`: 新增 `loop_exhausted` flag |

### 测试增长

| 指标 | 优化前 | 优化后 |
|------|--------|--------|
| Agent 测试总数 | 54 | **70** (+16) |
| 全部通过 | 否（1 预存失败） | **是** |

### 评分变化

| 维度 | 修复前 | 修复后 |
|------|--------|--------|
| 上下文管理 | 5/10 | **7.5/10** |
| 工具系统 | 7/10 | **8.5/10** |
| 错误处理 | 6/10 | **7.5/10** |
| 测试质量 | 7/10 | **8/10** |
| 代码风格 | 7/10 | **7.5/10** |
| **综合** | **7.2/10** | **8.0/10** |

---

## 五、建议下一步

1. **补测试盲区**（2-3h）— 提 `FakeLLM` 到 `conftest.py`，补 context overflow 降级 + `_is_retryable_exception` + `session_extract` 测试
2. **加 CI**（0.5h）— GitHub Actions 配好，每次 push 自动跑 70+ 测试
3. **加 Linter + Type checker**（1h）— ruff + mypy 基础配置，CI 中强制
4. **Docker 化**（1h）— 一键 `docker-compose up` 启动全套服务
