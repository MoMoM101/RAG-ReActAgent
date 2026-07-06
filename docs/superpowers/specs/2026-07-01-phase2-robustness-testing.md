# Phase 2 — 测试与健壮性设计

**日期**: 2026-07-01 | **优先级**: 中 | **预计改动范围**: 8+ 文件

---

## 2.1 Agent Loop 核心测试（Mock LLM 注入）

### 问题
Agent Loop (`loop.py`) 零测试覆盖。任何修改都无法自动验证回归。

### 方案
Mock `create_llm()` 返回一个可控的 `FakeLLM`，注入预定的 `tool_calls` 和 `content`，覆盖 6 个场景：

| 测试场景 | 注入内容 | 验证点 |
|----------|----------|--------|
| 单轮工具调用 | 1 个 `search_docs` tool_call + 最终回答 | SSE 事件包含 tool_call/tool_result/done |
| 多轮工具调用 | 2+ 轮 tool_calls（先 search，再 calculator） | 每个 tool 都被执行，事件顺序正确 |
| 循环超限 (LOOP_LIMIT) | 每轮都返回 tool_call，共 10+ 轮 | 收到 LOOP_LIMIT error 事件 |
| 时间超限 (TIME_LIMIT) | 修改 start_time/max_total_time 或做慢速 tool | 收到 TIME_LIMIT error 事件 |
| 直接回答（无工具） | 返回 content 不带 tool_calls | answer_chunk 事件 + done |
| 工具执行失败 | tool_call 到不存在的工具 | tool_result 包含 error |

**FakeLLM 设计**:
```python
class FakeLLM:
    def __init__(self, responses: list[dict]):
        # responses: [{"content": "...", "tool_calls": [...]}, ...]
        self.responses = responses
        self.call_count = 0

    async def chat_stream(self, messages, tools=None):
        if self.call_count >= len(self.responses):
            yield StreamChunk(content="done")
            return
        resp = self.responses[self.call_count]
        self.call_count += 1
        if resp.get("content"):
            yield StreamChunk(content=resp["content"])
        if resp.get("tool_calls"):
            yield StreamChunk(tool_calls=resp["tool_calls"])
```

**文件**: 新增 `backend/tests/agent/test_loop.py`

---

## 2.2 意图分类 8 种规则边界测试

### 方案
纯函数测试（不依赖 LLM），覆盖 `_rule_match` 的所有分支：

| 规则 | 输入 | 期望 intent |
|------|------|-------------|
| 确认词 | "好的" (有历史) | acknowledgment |
| 确认词-无历史 | "好的" (无历史) | None (走 LLM) |
| 短追问 | "这个呢" (有历史) | context_followup |
| 追问标记 | "还有呢" (有历史) | context_followup |
| 中等追问 | "那之前提到的那个文件" (有历史, ≤30字符) | possible_followup |
| 计算 | "1+2等于" | calculation |
| 文档列表 | "有哪些文档" | document_listing |
| 无匹配 | "量子力学是什么" | None |
| 确认词集合 | "谢谢" | acknowledgment |
| 噪音边界 | "帮我算一下3*4" | calculation |

**文件**: 新增 `backend/tests/agent/test_classifier.py`

---

## 2.3 记忆拦截正则/LLM 分支测试

### 方案

| 测试 | 输入 | 验证 |
|------|------|------|
| 正则-我叫 | "我叫张三" | 提取到 ("用户叫张三", "identity") |
| 正则-我是 | "我是工程师" | 提取到 ("用户是工程师", "identity") |
| 正则-喜欢 | "我喜欢Python" | 提取到 ("用户喜欢Python", "preference") |
| 正则-决定 | "我决定用FastAPI" | 提取到 ("用户决定用FastAPI", "decision") |
| 正则-噪音过滤 | "我叫外卖" | 不提取（噪音词） |
| 正则-超长截断 | "我叫" + 100字 | 不提取（>80字符） |
| 正则-多条 | "我叫张三，我喜欢Python" | 提取到 2 条 |
| 批量确认 | 2条候选 | 1次 LLM 调用返回 indices |

**文件**: 新增 `backend/tests/agent/test_intercept.py`

---

## 2.4 Bing 搜索容错 + 日志

### 问题
`tools.py` 中 Bing 搜索使用硬编码 CSS 选择器 `li.b_algo, h2 a, .b_caption p`，Bing 前端改版即失效。当前静默失败。

### 方案
在 HTML 解析失败时：
1. 记录 warn 日志（含 query 和 HTTP 状态码）
2. 自动降级到 DDGS 兜底

```python
try:
    bing_results = _parse_bing(html)
    if not bing_results:
        logger.warning(f"Bing HTML parsing returned 0 results, query={query!r}")
except Exception as e:
    logger.warning(f"Bing HTML parsing failed: {e}, query={query!r}, status={resp.status_code}")
    bing_results = []
```

**文件**: `backend/agent/tools.py`（WebSearch 函数区域）

---

## 2.5 前端输入长度限制

### 问题
`ChatRequest.message max_length=10000`，超限返回 422，用户看到的是不可读的错误信息。

### 方案
1. 后端 `chat.py` 中 422 响应增加友好提示
2. 前端 ChatInput 组件加 `maxLength={4000}` + 剩余字符计数器

**文件**:
- `backend/api/chat.py`
- `frontend/src/components/chat/ChatInput.tsx`（路径需确认）

---

## 2.6 session_extract 按消息数截断

### 问题
`session_extract.py:104` 硬截断对话文本到 6000 字符，可能截断中间的 key-value 信息。

### 方案
改为按"最近 N 条用户+assistant 消息对"截断，而非按字符数：

```python
# 旧: conversation_text[:6000]
# 新: 取最近 N 条消息（如 20 条），每条截断到 200 字符
messages = conversation_text.split("\n")
recent = messages[-20:]  # 最近 20 条
text = "\n".join(recent)
```

**文件**: `backend/agent/session_extract.py`

---

## 2.7 Chunk 质量评分开关

### 问题
`retriever.py:101-132` 的 `_chunk_quality_score` 对代码/API 文档/多级标题等正常内容误判为低质量。

### 方案
加配置开关 `chunk_quality_filter_enabled: bool = True`（默认开启），用户可在 `.env` 或设置页关闭。

```python
def _chunk_quality_score(text: str) -> float:
    if not settings.chunk_quality_filter_enabled:
        return 1.0  # 跳过评分
    # ... 原有逻辑
```

**文件**:
- `backend/config.py` — 加配置项
- `backend/rag/retriever.py` — 加开关判断

---

## 影响范围汇总

| # | 改动 | 文件 | 类型 |
|---|------|------|------|
| 2.1 | Agent Loop 测试 | `tests/agent/test_loop.py` (新) | 测试 |
| 2.2 | 意图分类测试 | `tests/agent/test_classifier.py` (新) | 测试 |
| 2.3 | 记忆拦截测试 | `tests/agent/test_intercept.py` (新) | 测试 |
| 2.4 | Bing 容错+日志 | `agent/tools.py` | 后端 |
| 2.5 | 前端输入限制 | `api/chat.py`, `ChatInput.tsx` | 后端+前端 |
| 2.6 | session_extract | `agent/session_extract.py` | 后端 |
| 2.7 | Chunk 质量开关 | `config.py`, `rag/retriever.py` | 后端 |

---

## 实施顺序

2.4 → 2.5 → 2.6 → 2.7 → 2.2 → 2.3 → 2.1

先做简单改动（2.4-2.7），再做测试（2.2-2.3），最后做最复杂的 Agent Loop 测试（2.1）。
