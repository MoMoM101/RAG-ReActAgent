# Phase 1.2 — LLM 调用次数削减设计

**日期**: 2026-07-01 | **优先级**: 高 | **预计改动范围**: 2 文件

---

## 问题描述

当前单次用户消息的 LLM 调用路径（最坏情况）：

| 步骤 | 调用 | 位置 | 是否必需 |
|------|------|------|----------|
| 1 | `_llm_classify` — 意图分类 | `classifier.py:108` | 规则命中时跳过 |
| 2 | `confirm_memory` — 每条候选记忆确认 | `intercept.py:63` | 有候选时调用，每条一次 |
| 3 | `llm.chat_stream` — Agent 主循环 | `loop.py:100` | 必需 |
| 4 | `_extract_with_llm` — 会话结束提取 | `session_extract.py:72` | 后台异步，非常规路径 |
| 5 | 记忆预加载 embedding | `loop.py:49-59` | 每次都执行 |

最坏情况：步骤 1 + 步骤 2×N + 步骤 3 + 步骤 5 embedding = **多次 LLM 调用 + 额外 embedding 开销**。

---

## 设计方案

### 改动 1：合并意图分类 + 记忆确认为单次调用

**现状**: `classify_intent` 和 `confirm_memory` 是两次独立的 LLM 调用，即使它们处理的是同一条用户消息。

**方案**: 将 `decide_memory` 的判断逻辑合并到 `classify_intent` 的 tool definition 中，让一次 LLM 调用同时返回 intent + memory save 决策。

具体做法：
- 扩展 `INTENT_TOOL` 的 `save_to_profile` 字段，增加每条候选的 `should_save` 布尔标记
- 当 `classify_intent` 返回 `save_to_profile` 中有 `should_save=true` 的项时，直接写入画像，不再单独调 `confirm_memory`
- 正则提取的候选（`extract_memory_candidates`）仍走单独的 `confirm_memory`（但此时 LLM 已经调用过了，可以用同一结果）

**简化版方案**（推荐）：不改变 tool schema，而是在 loop.py 中调整逻辑顺序：

1. 先合并调用：将正则候选传给 `_llm_classify` 的 system prompt，让 LLM 同时判断哪些候选值得保存
2. 减少 `confirm_memory` 的调用次数：将多条候选拼接为一条消息，一次调用判断所有

```python
# loop.py 改动示意
# 原逻辑：先 classify，再逐条 confirm_memory
# 新逻辑：先走规则分类，然后合并候选一次性 LLM 确认

candidates = extract_memory_candidates(user_message)

# 规则命中 → 跳过 LLM 分类
hint = classify_intent(user_message, conversation_history)

if hint.intent == "_llm_needed" or candidates:
    # 一次 LLM 调用同时做分类 + 记忆确认
    hint, confirmed = await _combined_classify_and_confirm(
        user_message, conversation_history, candidates
    )
    # 合并 LLM 分类器提取的 save_to_profile
    for item in (hint.save_to_profile or []):
        c = item.get("content", ""); t = item.get("type", "fact")
        if c and (c, t) not in candidates:
            candidates.append((c, t))
            confirmed.append((c, t))
else:
    # 规则命中且无候选记忆，不需要 LLM
    confirmed = []
    for candidate, mem_type in candidates:
        if await confirm_memory(candidate):
            confirmed.append((candidate, mem_type))
```

### 改动 2：记忆预加载按需触发

**现状** (`loop.py:49-59`):
```python
if any(t in hint.suggested_tools for t in ("recall_memory",)):
    from memory.profile import search_profile
    recalled = await search_profile(user_message, top_k=5)
```

search_profile 会触发 embedding 调用。当前只要 suggested_tools 中包含 recall_memory 就会执行。

**方案**: 增加 `hint.intent == "personal_memory"` 前置条件：

```python
if hint.intent == "personal_memory" and any(
    t in hint.suggested_tools for t in ("recall_memory",)
):
    ...
```

这样只有用户明确问"我是谁/还记得吗"时才触发 embedding，闲聊/知识检索场景不会浪费 embedding 调用。

### 改动 3（可选）：confirm_memory 批量化

当正则提取到多条候选时（`len(candidates) > 1`），将它们拼接为一条 LLM 消息：

```python
async def confirm_memories_batch(candidates: list[tuple[str, str]]) -> list[tuple[str, str]]:
    """批量确认多条候选记忆。"""
    if not candidates:
        return []
    combined = "\n".join(f"{i+1}. {c}" for i, (c, _) in enumerate(candidates))
    # ... 一次 LLM 调用，返回 [true, false, true, ...]
```

此改动为可选优化，视 Phase 1 改动 1 的效果决定是否需要。

---

## 影响范围

| 文件 | 改动 |
|------|------|
| `backend/agent/loop.py` | 调整 LLM 调用顺序，增加合并调用逻辑，记忆预加载加条件 |
| `backend/agent/classifier.py` | 可能新增 `_combined_classify_and_confirm` 函数 |

无 API 变更，无前端变更。

---

## 调用次数对比

| 场景 | 优化前 | 优化后 |
|------|--------|--------|
| 知识检索（规则命中，无记忆） | 1 (主循环) | 1 (主循环) |
| 知识检索（规则未命中） | 2 (分类 + 主循环) | 2 (合并分类+确认 + 主循环) |
| 个人信息透露 | 3-5 (分类 + 确认×1-3 + 主循环) | 2 (合并分类+确认 + 主循环) |
| 个人信息查询 | 2 (分类 + embedding + 主循环) | 1-2 (仅 personal_memory 时做 embedding) |

---

## 验证方式

1. 启动后端，发送知识检索问题 → 确认响应正常
2. 发送个人信息（"我叫测试"） → 确认记忆保存正常
3. 发送"我是谁" → 确认记忆召回正常
4. 观察终端日志中 LLM 调用次数减少
5. 运行现有测试：`pytest backend/tests/ -v`
