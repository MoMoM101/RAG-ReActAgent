# Agent 子系统深度测评报告

> 测评日期：2026-07-04
> 范围：`backend/agent/` 全部文件 + `backend/llm/base.py` + 相关测试

---

## 一、架构总览

```
用户消息
  │
  ├─ [classifier.py]  意图分类 (规则优先 → LLM 兜底)
  │     └─ _rule_match()     acknowledgement / context_followup / calculation / document_listing
  │     └─ llm_classify()    personal_memory / knowledge_retrieval / web_search / general_chat
  │
  ├─ [intercept.py]   记忆拦截 (正则提取候选 → 批量 LLM 确认 → 写入画像)
  │
  └─ [loop.py]        ReAct 主循环 (SSE 流式输出)
        ├─ 构建 system prompt     = context.py.build_system_prompt()
        ├─ while iteration < max  (默认 10 轮)
        │    ├─ 裁剪消息           = context.py.trim_messages()
        │    ├─ LLM 流式调用       → answer_chunk / thought / tool_call
        │    ├─ 工具执行           = tools.py.ToolRegistry.execute()
        │    ├─ 追加 tool 结果     → 下一轮对话
        │    └─ context overflow?  → context_window //= 2 重试
        └─ 达上限                → 强制 LLM 总结 / 兜底文案
```

**设计模式**：ReAct (Reasoning + Acting)，Agent 通过"思考→行动→观察→思考"的循环完成任务。

---

## 二、逐模块评价

### 2.1 `classifier.py` ⭐⭐⭐⭐ — 意图分类

| 维度 | 评价 |
|------|------|
| 设计思路 | 规则优先 + LLM 兜底，业界最佳实践 |
| 规则覆盖 | 4 种确定性规则 + 1 种概率性规则 |
| 测试覆盖 | 9 类共 16 个用例，规则/边界完整 |
| LLM 兜底 | 通过 tool calling 返回结构化分类结果 |
| 评分 | **7.5 / 10** |

**规则匹配表**：

| 意图 | 触发关键词/条件 | 置信度 | 推荐工具 |
|------|-----------------|--------|----------|
| acknowledgement | `好的/嗯/ok/明白了/谢谢` + 有历史 | 0.9 | 无 |
| context_followup | 含指代词或 ≤12字符 + 有历史 | 0.85 | search_docs |
| possible_followup | ≤30字符 + 有历史 | 0.5 | search_docs |
| calculation | 含计算关键词 + 数字 | 0.7 | calculator |
| document_listing | `有哪些文档/列出文档/文档列表` 等 | 0.7 | list_documents |
| _llm_needed | 以上均不命中 | — | LLM 分类 |

**⚠️ 注意**：第 3 条 `possible_followup` 规则用 `len(query) ≤ 30` 做阈值，会将"我今天中午吃了什么"也判定为追问。30 是经验值，不是统计值。

---

### 2.2 `intercept.py` ⭐⭐⭐⭐ — 记忆拦截

| 维度 | 评价 |
|------|------|
| 提取能力 | 6 种正则模式覆盖身份/偏好/决策/事实 |
| 噪音过滤 | 9 个黑名单词防误触发 |
| LLM 确认 | 批量模式：N 条候选 1 次 API 调用 |
| 测试覆盖 | 7 类共 20 个用例 |
| 评分 | **8 / 10** |

**流程**：

```
用户输入："我叫张三，我喜欢Python，我决定用FastAPI"
  │
  ├─ 正则提取：[(用户叫张三, identity), (用户喜欢Python, preference), (用户决定用FastAPI, decision)]
  │
  ├─ 噪音过滤：所有值不在 _NOISE_WORDS 中 → 全部通过
  │
  └─ 批量 LLM 确认：decide_memories(tool_call) → save_indices: [1, 2, 3] → 全部写入画像
```

**🏆 噪音词机制是整个 Agent 最精巧的工程细节**。9 个黑名单词（`外卖/快递/电话/车/说/讲/看一下/问一下/睡了/走了/到了/完了/好了`）解决了"我叫外卖""我喜欢说唱"等常见的正则误匹配。

**⚠️ 局限性**：噪音词是穷举维护的，不能覆盖所有边缘情况。比如"我叫滴滴""我叫车"会穿透。

---

### 2.3 `context.py` ⭐⭐⭐ — 上下文管理

| 维度 | 评价 |
|------|------|
| System Prompt | 结构好（铁律→工作流→工具→风格），中文 prompt 工程成熟 |
| Token 估算 | `len(text) // 2` — 过于粗糙 |
| 消息裁剪 | 从后往前滑动窗口，保证最新消息优先 |
| 测试覆盖 | 无独立单元测试 |
| 评分 | **6 / 10** |

**🔴 主要问题：Token 估算不准**

当前估算方式 `len(text) // 2` 的偏差：

| 文本类型 | 实际 token 数 | 估算值 | 偏差 |
|----------|--------------|--------|------|
| 纯中文 100 字 | ~65 tokens (`cl100k_base`) | 50 | 低估 23% |
| 纯英文 100 字符 | ~30 tokens | 50 | **高估 67%** |
| 代码混合 | 取决于 token 密度 | 偏差不可控 | — |

对中文偏保守（浪费预算），对英文偏乐观（可能触发 context overflow 走降级路径）。**项目已依赖 `tiktoken`（splitter.py 用），但没有注入到 ContextManager 中。**

**⚠️ 消息裁剪的配对逻辑**：

`trim_messages()` 在遇到 tool 消息时会向上查找配对的 assistant（含 tool_calls）打包处理。但如果 LLM 一次返回 3 个 tool_calls，执行时只有 2 个 tool 结果消息（第 3 个还没执行完），则配对逻辑只找到 2 个 tool 结果，第 3 个丢失。这是极端情况但可能出现。

---

### 2.4 `context_window.py` ⭐⭐⭐⭐ — 上下文窗口检测

| 维度 | 评价 |
|------|------|
| 模型覆盖 | `model_contexts.json` 覆盖 40+ 模型 |
| 匹配策略 | 精确匹配 → 前缀匹配 → 默认值 |
| 运行时探测 | 无（用硬编码替代，务实选择） |
| 溢出处理 | `context_window //= 2` 降级，最低 16000 |
| 评分 | **8 / 10** |

**设计良好**：三层层级（`.env` 覆盖 > JSON 映射 > 默认 128K），`is_context_error()` 用关键词匹配判断 API 返回的错误是否为上下文超限。降级策略 `window //= 2` 是标准的指数退避变体。

**⚠️ 局限性**：没有运行时探测（发一条空消息测试实际窗口大小）。但对于开源项目，硬编码 + 手动覆盖是务实选择，避免了启动时的额外 API 调用。

---

### 2.5 `loop.py` ⭐⭐⭐⭐ — ReAct 主循环

| 维度 | 评价 |
|------|------|
| 流程正确性 | ReAct 循环 + SSE 流式输出，主路径正确 |
| 边界处理 | 超时 + 循环上限 + context overflow + LLM 调用失败 |
| 来源提取 | 从最后一个 search_docs 结果提取 |
| 后台任务 | `_process_dropped` 处理被裁剪消息的画像提取 |
| 测试覆盖 | 5 类场景（直接回答/工具调用/失败/来源/上限） |
| 评分 | **8 / 10** |

**🏆 亮点 — search_docs 结果的强制约束**：

```python
# loop.py:192-197
if tc.name == "search_docs" and result.success:
    result_text = (
        "【以下是你唯一可以使用的回答来源。只能引用这些内容回答用户，"
        "禁止使用你自己的知识或训练数据中的信息。"
        "如果以下内容不足以回答问题，如实告知用户。】\n"
        + result_text
    )
```

这是防止 LLM 幻觉的关键机制。不依赖 system prompt 的"软约束"，而是将约束直接注入工具返回的上下文中，LLM 无法忽略。

**⚠️ 问题 1 — 循环上限后的兜底文案**：

```python
# 强制总结失败
yield {"event": "answer_chunk", "data": {"delta": "抱歉，思考轮次已达上限，且自动总结失败。..."}}
```

上限到了 + LLM 调用失败 → 同一个固定文案。用户无法区分是"上限触发"还是"总结失败后又兜底"。应该用不同的消息或至少在 error 事件中包含区分信息。

**⚠️ 问题 2 — `_process_dropped` 静默失败**：

```python
async def _process_dropped(queries: list[str]):
    try:
        ...
    except Exception:
        pass  # ← 静默丢弃所有错误
```

被裁剪的用户消息本可以用来提取画像信息，但如果这个后台任务失败（DB 连接断开、LLM 不可用等），没有任何日志，画像信息永久丢失。

---

### 2.6 `tools.py` ⭐⭐⭐⭐ — 工具系统

| 工具 | 功能 | 安全 | 评分 |
|------|------|------|------|
| `SearchDocsTool` | 混合检索 + 文档过滤 + 文件名关联 | ⚠️ 输入无校验 | 8/10 |
| `CalculatorTool` | AST 白名单安全计算 | ✅ 范例级 | **9/10** |
| `ListDocumentsTool` | 文档列表（无分页） | ✅ 只读 | 7/10 |
| `GetDocumentInfoTool` | 文档详情 | ✅ 只读 | 7/10 |
| `WebSearchTool` | Bing + DDGS 双引擎降级 | ⚠️ httpx 同步调用 | 7/10 |
| `RecallMemoryTool` | 画像语义搜索 + 直匹配兜底 | ✅ 只读 | 7/10 |

#### 🏆 CalculatorTool — 项目最值得展示的安全代码

```python
def _is_allowed(self, node):
    if isinstance(node, ast.Constant):
        return isinstance(node.value, (int, float))  # 只允许数字字面量
    if isinstance(node, ast.UnaryOp):
        return isinstance(node.op, ast.USub) and self._is_allowed(node.operand)
    if isinstance(node, ast.BinOp):
        return (isinstance(node.op, (ast.Add, ast.Sub, ast.Mult, ast.Div))
                and self._is_allowed(node.left) and self._is_allowed(node.right))
    return False  # 拒绝所有其他结构
```

逐节点 AST 白名单遍历，以下攻击全部被拦截：
- `__import__('os').system('dir')` → 拒绝（函数调用不是 Constant）
- `obj.attr` → 拒绝（属性访问不在白名单）
- `[1,2][0]` → 拒绝（列表不在白名单）

测试也覆盖了这两个攻击向量。

#### 🔴 严重问题 — 工具重试机制是死代码

**每个工具的实现模式**：

```python
async def execute(self, ...) -> ToolResult:
    try:
        ...
        return ToolResult(success=True, ...)
    except Exception as e:
        return ToolResult(success=False, error=str(e))  # ← 吞掉所有异常
```

**ToolRegistry 的指数退避重试**：

```python
# ToolRegistry.execute()
for attempt in range(max_retries + 1):
    try:
        result = await tool.execute(**kwargs)  # ← 永远成功返回（ToolResult）
        return result
    except Exception as e:  # ← 永远不会触发！
        ...
```

工具内部 `except Exception` 将所有网络/超时错误都转换成了 `ToolResult(success=False)`，外层重试循环的 `except` 永远捕获不到异常。`SearchDocsTool.retry_strategy = "exponential"` 和 `max_retries = 3` 是两个**装饰性配置**。

**影响范围**：所有 6 个工具的重试配置均无效。Qdrant 断连、embedding API 超时、Bing 网络波动 — 都不会触发重试。

**修复方向**：
```
工具.execute() 只 catch 业务逻辑错误（文档不存在等）
网络/超时类异常 let it raise → ToolRegistry 指数退避重试
```

#### ⚠️ 问题 2 — WebSearchTool 的 HTTP 调用方式

```python
# tools.py:356
resp = await asyncio.to_thread(
    lambda: httpx.get(...)  # 同步 HTTP → 跑在线程池里
)
```

`httpx` 本身支持 `AsyncClient`，这里强行把 async-capable 库用同步方式跑在线程池，效率低且不符合项目其他部分的 async 风格。

---

## 三、安全问题

| 检查项 | 状态 | 说明 |
|--------|------|------|
| 工具参数注入 | ✅ 安全 | JSON Schema 校验 + AST 白名单 |
| API Key 泄露 | ✅ 安全 | AES-256-GCM 加密 + 掩码展示 |
| 工具滥用 | ✅ 安全 | 30/min chat、10/min upload 限流 + 10 轮循环上限 |
| Prompt Injection | ⚠️ 部分 | system prompt 无输入转义，但强制约束前缀降低了风险 |
| SQL 注入 | ⚠️ 部分 | Agent 内部无此问题，但 `api/documents.py:134` 有字符串拼接（有正则前置校验，非 Agent 范围） |

---

## 四、错误处理韧性

| 层次 | 处理方式 | 评价 |
|------|---------|------|
| 工具执行失败 | `ToolResult(success=False)` | ✅ 正确 |
| LLM 调用失败 | `is_context_error()` 判断 → 降级重试 | ✅ 正确 |
| 上下文溢出 | `context_window //= 2`，最低 16000 | ✅ 正确 |
| 循环上限触发 | 强制 LLM 总结 → 固定文案兜底 | ✅ 可用 |
| 后台任务失败 | `except Exception: pass` | ❌ 静默丢弃 |

---

## 五、测试覆盖

| 测试文件 | 测试数 | 覆盖范围 | 质量 |
|----------|--------|----------|------|
| `tests/agent/test_classifier.py` | 16 | 确认/追问/计算/文档 4 类，边界完整 | ⭐⭐⭐⭐⭐ |
| `tests/agent/test_intercept.py` | 20 | 身份/偏好/决策/事实/噪音/长度/多条 | ⭐⭐⭐⭐⭐ |
| `tests/agent/test_tools.py` | 13 | 计算器安全/工具列表/文档信息 | ⭐⭐⭐⭐ |
| `tests/agent/test_loop.py` | 5 | 直接回答/工具调用/失败/来源/上限 | ⭐⭐⭐⭐ |

**🏆 FakeLLM 注入模式** (`test_loop.py`)：

```python
class FakeLLM(BaseLLM):
    def __init__(self, response_queues: list[list[LLMResponse]]):
        self.queues = response_queues  # 预定义的多轮响应队列
        self.call_index = 0

    async def chat_stream(self, messages=None, tools=None):
        queue = self.queues[self.call_index]
        self.call_index += 1
        for resp in queue:
            yield resp
```

不依赖真实 API，通过队列注入精确控制 LLM 的每轮输出。这是一个优秀的测试设计。

**缺失的测试**：
- Context overflow 降级路径（`window //= 2` → 重试 → 成功/失败）
- `_process_dropped` 被裁剪消息的画像提取
- 多工具并行调用（LLM 一次返回多个 tool_calls）
- `trim_messages` 的 Token 预算边界

---

## 六、改进建议（按优先级排列）

### P0 — 修了才说得过去

| # | 项目 | 当前行为 | 应改 |
|---|------|---------|------|
| 1 | 工具重试 | 工具内部 catch 所有异常，Registry 重试永不被触发 | 工具只 catch 业务错误，网络/超时 raise 给 Registry 层重试 |
| 2 | Token 估算 | `len(text) // 2` | 注入 tiktoken，复用 splitter.py 已有的 `cl100k_base` encoder |

### P1 — 有损专业度，修了加分

| # | 项目 | 当前行为 | 应改 |
|---|------|---------|------|
| 3 | 后台任务 | `except Exception: pass` | 至少打 error 日志 |
| 4 | 裁剪掉的消息 | 直接丢弃 | 纳入 `_process_dropped` 做画像提取（已实现但可优化） |
| 5 | WebSearchTool | `asyncio.to_thread(httpx.get)` | 改用 `httpx.AsyncClient` |
| 6 | System Prompt | 中文硬编码 | 考虑提取为模板文件，支持 i18n |

### P2 — 有了更好

| # | 项目 | 当前行为 | 应改 |
|---|------|---------|------|
| 7 | 循环上限兜底 | 固定文案 | 区分"上限触发"vs"总结失败" |
| 8 | `__init__.py` | 空文件 | 导出关键类提高 import 体验 |
| 9 | 多工具并行 | 未测试 | 补充测试覆盖 |
| 10 | context_window 运行时探测 | 无 | 可选：发空消息测实际窗口 |

---

## 七、总评

| 维度 | 评分 | 说明 |
|------|------|------|
| 架构设计 | 8/10 | ReAct 模式实现完整，职责拆分清晰 |
| 意图分类 | 7/10 | 规则覆盖好，中等追问阈值略激进 |
| 记忆系统 | 8/10 | 正则+LLM 两阶段确认，噪音过滤精巧 |
| 上下文管理 | 5/10 | Token 估算粗糙，裁剪逻辑有边缘情况 |
| 工具系统 | 7/10 | 设计意图好但重试是死代码，Calculator 是安全典范 |
| 安全设计 | 8/10 | AST 白名单范例级，API Key 加密存储 |
| 测试质量 | 7/10 | 覆盖好但缺关键路径（降级、多工具并行） |
| 错误处理 | 6/10 | 主路径健全，后台任务静默丢弃 |
| 代码风格 | 7/10 | 类型注解好，`__init__.py` 为空 |

### **Agent 子系统综合评分：7.2 / 10**

### 核心判断

**这是一个合格的 ReAct Agent 实现，有工程亮点的个人作品，但离生产级差半口气。**

三个值得放在 GitHub README 里展示的技术点：
1. **CalculatorTool AST 白名单** — LLM Agent 安全性的教科书级实现
2. **记忆拦截两阶段设计** — 正则快筛 + LLM 精判 + 语义去重，完整闭环
3. **FakeLLM 测试注入** — 不依赖真实 API 的 Agent 测试方法

三个拖后腿的点：
1. **工具重试是死代码** — 设计意图和实现的断层
2. **Token 估算太粗糙** — 有 tiktoken 却不用
3. **后台任务静默失败** — 生产级运维的绊脚石

修复 P0 两项（重试机制 + Token 精确计算）后，Agent 子系统可达到 **8.0/10**，属于开源 RAG 项目中 Agent 设计的优秀水平。
