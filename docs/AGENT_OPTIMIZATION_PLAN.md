# Agent 子系统优化方案

> 基于 `AGENT_EVALUATION.md` 测评报告（综合评分 7.2/10）
> 目标：修复 P0/P1 后达到 **8.0/10**

---

## 一、总览

| 优先级 | 数量 | 预估工时 | 目标 |
|--------|------|---------|------|
| P0 — 必须修 | 2 | 3-4h | 消除死代码 + 精确 Token 管理 |
| P1 — 应该修 | 4 | 3-4h | 生产级错误处理 + HTTP 异步化 + 模板化 |
| P2 — 锦上添花 | 4 | 2-3h | 边界清晰化 + 测试补全 |

---

## 二、P0 — 修复方案

### P0-1: 修复工具重试死代码

**问题**：6 个工具的 `execute()` 内部 `except Exception` 吞掉所有异常返回 `ToolResult(success=False)`，导致 `ToolRegistry` 的指数退避重试（`for attempt in range(max_retries+1)` 的 `except Exception`）永远捕获不到异常。重试配置是装饰性代码。

**根因**：工具层和 Registry 层的异常边界模糊 — 工具把基础设施错误（网络超时、连接断开）和业务错误（文档不存在）同等处理。

**修改方案**：

```
文件: backend/agent/tools.py

1. 新增 RetryableError 异常类（放在 BaseTool 上方）

2. 逐个工具改造 — 只 catch 业务错误，让基础设施错误 raise：
   - SearchDocsTool:  只 catch 业务逻辑错误，Qdrant/embedding 网络错误 raise RetryableError
   - ListDocumentsTool:   只 catch "文档不存在"，DB 连接错误 raise
   - GetDocumentInfoTool: 同上
   - WebSearchTool:   asyncio.TimeoutError → raise RetryableError
                      网络错误 → raise RetryableError
                      HTML 解析失败 → ToolResult(success=False)（不重试）
   - RecallMemoryTool:    只 catch 业务逻辑错误，Qdrant 连接错误 raise
   - CalculatorTool:  已有 retry_strategy="none"，不变

3. ToolRegistry.execute() —
   当前捕获 Exception 做重试是对的，加上对 RetryableError 的显式处理，
   非 RetryableError 的普通异常也重试（宁可多试不漏）。
```

**核心思路**：

```
业务错误（不会自愈）     → ToolResult(success=False)  → 直接返回，不重试
网络/连接错误（可能自愈） → raise RetryableError       → Registry 指数退避重试
```

**具体判定规则**：

| 工具 | 不重试（业务错误） | 重试（基础设施错误） |
|------|-------------------|---------------------|
| SearchDocsTool | 无 | Qdrant 超时/断连、Embedding API 超时 |
| ListDocumentsTool | 文档不存在 | DB 连接断开 |
| GetDocumentInfoTool | 文档不存在 | DB 连接断开 |
| WebSearchTool | HTML 解析失败、Bing 被封 | HTTP 超时、网络错误、DDGS 超时 |
| RecallMemoryTool | 无 | Qdrant 超时/断连 |

**测试补充**：新增 `test_tool_retry.py`，Mock 网络层验证：
- 第 1 次失败 → 第 2 次成功 → ToolResult.success=True, retries=1
- 全部失败 → ToolResult.success=False, retries=max_retries

---

### P0-2: Token 精确估算

**问题**：`context.py:_estimate_tokens()` 用 `len(text) // 2`，对中文低估 23%，对英文高估 67%。项目已在 `splitter.py` 中依赖 `tiktoken`（`cl100k_base` encoder），但没有注入到 ContextManager。

**修改方案**：

```
文件: backend/agent/context.py

1. 新增模块级 _encoder 懒加载：
   import tiktoken
   _encoder: tiktoken.Encoding | None = None

   def _get_encoder() -> tiktoken.Encoding:
       global _encoder
       if _encoder is None:
           _encoder = tiktoken.get_encoding("cl100k_base")
       return _encoder

2. 替换 _estimate_tokens()：
   def _estimate_tokens(text: str | None) -> int:
       if not text:
           return 0
       return len(_get_encoder().encode(text))

3. 删除旧注释"简单 token 估算…"。
```

**影响分析**：
- ContextManager 的滑动窗口裁剪会更精确，不再高估英文截断过多、低估中文触发 context overflow
- `tiktoken` 是纯 Python 包，不增加系统依赖
- 首次调用时懒加载 encoder，不影响启动速度

**测试补充**：在现有测试基础上新增 token 估算精度验证（中文 100 字 ≈ 60-70 tokens，英文 100 字符 ≈ 25-35 tokens）。

---

## 三、P1 — 修复方案

### P1-1: 后台任务失败日志

**问题**：`loop.py:_process_dropped()` 第 275 行 `except Exception: pass` 静默丢弃所有错误，被裁剪消息中的画像信息永久丢失且无感知。

**修改方案**：

```
文件: backend/agent/loop.py, 函数 _process_dropped()

将:
    except Exception:
        pass

改为:
    except Exception:
        logger.warning(
            "_process_dropped failed for %d queries, memory extraction skipped",
            len(queries), exc_info=True
        )
```

### P1-2: WebSearchTool 异步 HTTP

**问题**：`tools.py:355-370` 用 `asyncio.to_thread(lambda: httpx.get(...))` 将原生支持 async 的 httpx 强行同步调用跑在线程池里，效率低且不符合项目 async 风格。

**修改方案**：

```
文件: backend/agent/tools.py, WebSearchTool._search_bing()

改为使用 httpx.AsyncClient：

async with httpx.AsyncClient(timeout=httpx.Timeout(10, connect=5),
                              follow_redirects=True) as client:
    resp = await asyncio.wait_for(
        client.get(
            "https://cn.bing.com/search",
            params={"q": query, "count": max_results},
            headers={...},
        ),
        timeout=12,
    )

对 _search_ddgs 中的 ddgs 库调用（同步库无法改），保留 asyncio.to_thread。
```

### P1-3: System Prompt 模板化

**问题**：`context.py:build_system_prompt()` 中的中文 prompt 硬编码在代码中，不方便维护和多语言扩展。

**修改方案**：

```
文件: backend/agent/prompts/system.txt（新建）

将 system prompt 文本提取为模板文件，用 {placeholder} 标记变量：
- {profile_section}
- {tools_description}
- {hint_section}

文件: backend/agent/context.py

build_system_prompt() 从文件加载模板，str.format() 替换变量。
首次加载后缓存，避免重复 I/O。
```

### P1-4: 裁剪消息降级日志

**问题**：`context.py:trim_messages()` 裁剪掉早期消息时没有日志，运维排查困难。

**修改方案**：

```
文件: backend/agent/context.py, trim_messages()

当 dropped_queries 非空时，添加 info 日志：
logger.info(
    "trim_messages: dropped %d messages (%d chars), kept %d messages",
    len(dropped_queries), sum(len(q) for q in dropped_queries), len(kept)
)
```

---

## 四、P2 — 修复方案

### P2-1: 区分循环上限兜底消息

**问题**：`loop.py:237` 上限触发 + 总结失败 → 同一个固定文案，用户无法区分情况。

**修改方案**：

```
文件: backend/agent/loop.py

将兜底消息改为：
- 上限触发但总结成功 → 不额外提示（正常流）
- 上限触发 + 总结失败 → 
  yield {"event": "error", "data": {"code": "LOOP_LIMIT", "message": "思考轮次已达上限…"}}
  yield {"event": "answer_chunk", "data": {"delta": "抱歉，思考轮次已达上限，且自动总结失败。…"}}

同时在 status 事件中区分：
- "已达到最大思考轮次，正在整理答案…"（总结尝试中）
- "注意：已达到最大思考轮次，以上为自动总结"（总结成功）
- "注意：思考轮次已达上限且总结失败，以上为兜底回复"（总结失败）
```

### P2-2: `__init__.py` 导出

**问题**：`backend/agent/__init__.py` 为空文件。

**修改方案**：

```python
from agent.loop import run_agent_loop
from agent.classifier import classify_intent, llm_classify, IntentHint
from agent.intercept import extract_memory_candidates, confirm_candidates_batch
from agent.context import ContextManager

__all__ = [
    "run_agent_loop",
    "classify_intent",
    "llm_classify",
    "IntentHint",
    "extract_memory_candidates",
    "confirm_candidates_batch",
    "ContextManager",
]
```

### P2-3: 多工具并行调用测试

**问题**：当 LLM 一次返回多个 tool_calls 时（如同时 search_docs + recall_memory），缺少专门的测试覆盖。

**修改方案**：

```
文件: tests/agent/test_loop.py（追加）

新增 test_parallel_tool_calls:
- FakeLLM 第一轮返回 2 个 tool_calls: search_docs + recall_memory
- 验证两个 tool 都被执行
- 验证 tool_result 事件数量 = 2
- 验证最终 messages 中包含两个 tool 结果消息
```

### P2-4: Context window 运行时探测（可选）

**问题**：`context_window.py` 依赖硬编码的 `model_contexts.json`，新模型需要手动添加。

**修改方案**（低优先级，不建议立即做）：

如果需要，在启动时发送一条最小消息探测实际窗口（会增加一次 API 调用和启动延迟）。当前硬编码 + `.env` 覆盖的方案对开源项目已足够，此条可长期搁置。

---

## 五、实施顺序建议

```
Phase A（2h）: P0-1 + P0-2          ← 核心修复，评分从 7.2 → 7.8
Phase B（2h）: P1-1 → P1-2 → P1-3   ← 生产级完善
Phase C（1h）: P2-1 → P2-2 → P2-3   ← 边界清晰化
Phase D（1h）: 全部验证 + 测试       ← 回归 + 新测试
```

### 建议执行顺序

1. **P0-2 Token 估算** — 改动最小、影响面最可控的先做
2. **P0-1 工具重试** — 涉及 6 个工具 + 新增测试，改动量最大
3. **P1-1 后台日志** — 一行改动
4. **P1-2 httpx 异步化** — 改动集中在一个方法
5. **P1-3 Prompt 模板化** — 新建文件 + 小幅重构
6. **P1-4 裁剪日志** — 一行改动
7. **P2-1/P2-2/P2-3** — 依次完成

---

## 六、改动文件清单

| 文件 | P0-1 | P0-2 | P1-1 | P1-2 | P1-3 | P1-4 | P2-1 | P2-2 | P2-3 |
|------|:----:|:----:|:----:|:----:|:----:|:----:|:----:|:----:|:----:|
| `backend/agent/tools.py` | ● | | | ● | | | | | |
| `backend/agent/context.py` | | ● | | | ● | ● | | | |
| `backend/agent/loop.py` | | | ● | | | | ● | | |
| `backend/agent/__init__.py` | | | | | | | | ● | |
| `backend/agent/prompts/system.txt` | | | | | ● | | | | |
| `tests/agent/test_tools.py` | ● | | | | | | | | |
| `tests/agent/test_loop.py` | | | | | | | | | ● |

---

## 七、预期结果

| 维度 | 修复前 | 修复后 |
|------|--------|--------|
| 工具系统 | 7/10（重试是死代码） | 8.5/10（重试生效 + HTTP 异步化） |
| 上下文管理 | 5/10（Token 估算粗糙） | 7.5/10（tiktoken 精确 + 裁剪日志） |
| 错误处理 | 6/10（后台任务静默失败） | 7.5/10（完整日志覆盖） |
| 代码风格 | 7/10（__init__.py 为空） | 7.5/10（导出 + 模板化） |
| 测试质量 | 7/10（缺降级/并行） | 8/10（补重试 + 并行测试） |

**Agent 子系统综合评分：7.2/10 → 8.0/10**
