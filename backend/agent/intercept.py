"""Memory save pre-intercept — regex extraction + LLM confirmation via tool calling."""

import re

# 误触发噪音词 / 问题词
_NOISE_WORDS = {"外卖", "快递", "电话", "车", "说", "讲", "看一下",
                "问一下", "睡了", "走了", "到了", "完了", "好了"}
# 纯疑问词 — 匹配到的 value 如果是纯提问（"我是谁"→"谁"），不应该存
_QUESTION_CHARS = set("谁什么咋啥哪怎吗")  # 匹配到的 value 包含这些字 → 大概率是问题
# 整体都是提问的消息 — 正则匹配前就过滤
_IDENTITY_QUESTIONS = {"我是谁", "我叫什么", "我叫啥", "我是什么", "我是啥",
                        "你是谁", "你叫什么", "你是谁啊"}


def extract_memory_candidates(query: str) -> list[tuple[str, str]]:
    """正则提取个人信息候选，一条消息中可能有多条信息。返回 [(content, memory_type), ...]。"""

    # The entire message is an identity question ("我是谁"/"我叫什么") —
    # do not extract anything.
    if query.strip() in _IDENTITY_QUESTIONS:
        return []

    results: list[tuple[str, str]] = []
    seen = set()  # 去重：同一句式不重复匹配

    # 每条规则用 finditer 匹配所有出现位置，.+? 非贪婪防止多吃
    for pattern, fmt, mem_type in [
        (r"我(?:名字|的名)\s*(?:叫|是)\s*(.+?)(?:[，,。.！!；;\s]|$)", "用户叫{}", "identity"),
        (r"我叫\s*(.+?)(?:[，,。.！!；;\s]|$)", "用户叫{}", "identity"),
        (r"(?:你好|嗨|哈喽|hello)\s*我(?:叫|是)\s*(.+?)(?:[，,。.！!；;\s]|$)", "用户叫{}", "identity"),
        (r"我(?:职责|职业|工作|身份|专业|岗位|的职务|的职位)\s*(?:是\s*)?(.+?)(?:[，,。.！!；;\s]|$)", "用户是{}", "identity"),
        (r"(?:职业|工作|岗位)\s*(?:是|：)\s*(.+?)(?:[，,。.！!；;\s]|$)", "用户是{}", "identity"),
        (r"我(?:喜欢|爱)\s*(.+?)(?:[，,。.！!；;\s]|$)", "用户喜欢{}", "preference"),
        (r"我习惯\s*(.+?)(?:[，,。.！!；;\s]|$)", "用户习惯{}", "preference"),
        (r"我决定\s*(.+?)(?:[，,。.！!；;\s]|$)", "用户决定{}", "decision"),
        (r"我(?:项目是|在做)\s*(.+?)(?:[，,。.！!；;\s]|$)", "用户{}（项目/当前工作）", "fact"),
    ]:
        for m in re.finditer(pattern, query):
            value = m.group(1).strip("，。,.").strip()
            key = (mem_type, value)
            if key in seen:
                continue
            if value in _NOISE_WORDS:
                continue
            if _QUESTION_CHARS & set(value):  # value contains question characters
                continue
            if 1 <= len(value) <= 80:
                seen.add(key)
                results.append((fmt.format(value), mem_type))

    return results


# 向后兼容别名
def extract_memory_candidate(query: str) -> tuple[str, str] | None:
    candidates = extract_memory_candidates(query)
    return candidates[0] if candidates else None


MEMORY_DECISION_TOOL = {
    "type": "function",
    "function": {
        "name": "decide_memory",
        "description": "判断一条候选信息是否值得存入长期记忆。",
        "parameters": {
            "type": "object",
            "properties": {
                "save": {
                    "type": "boolean",
                    "description": "值得保存则 true，否则 false",
                },
            },
            "required": ["save"],
        },
    },
}


BATCH_MEMORY_TOOL = {
    "type": "function",
    "function": {
        "name": "decide_memories",
        "description": "判断多条候选信息中哪些值得存入长期记忆。",
        "parameters": {
            "type": "object",
            "properties": {
                "save_indices": {
                    "type": "array",
                    "items": {"type": "integer"},
                    "description": "值得保存的候选编号列表（从1开始）",
                },
            },
            "required": ["save_indices"],
        },
    },
}


async def confirm_memory(candidate: str) -> bool:
    """用 LLM (tool calling) 确认候选记忆是否值得保存。"""
    from llm.base import ChatMessage
    from llm.factory import create_llm

    system_prompt = """你是记忆保存确认器。判断信息是否值得存入长期记忆。

值得保存 -> save=true:
- 可能在未来对话中用到的事实、偏好、决定
- 关于用户身份、职业、习惯的明确信息

不值得保存 -> save=false:
- 临时性质的闲聊（"我叫外卖"、"我喜欢这首歌"）
- 模糊不确定的表述
- 常识性内容"""

    llm = create_llm()
    messages = [
        ChatMessage(role="system", content=system_prompt),
        ChatMessage(role="user", content=candidate),
    ]

    tool_calls_acc = []
    async for chunk in llm.chat_stream(messages, tools=[MEMORY_DECISION_TOOL]):
        if chunk.tool_calls:
            tool_calls_acc = chunk.tool_calls

    if tool_calls_acc:
        tc = tool_calls_acc[0]
        if tc.name == "decide_memory":
            return bool(tc.arguments.get("save", False))

    return False  # 无 tool call 或异常 -> 兜底不保存


async def confirm_candidates_batch(candidates: list[tuple[str, str]]) -> list[tuple[str, str]]:
    """一次 LLM 调用批量确认多条候选记忆。返回应保存的候选列表。"""
    if not candidates:
        return []
    if len(candidates) == 1:
        confirmed = await confirm_memory(candidates[0][0])
        return [candidates[0]] if confirmed else []

    from llm.base import ChatMessage
    from llm.factory import create_llm

    items_text = "\n".join(
        f"{i+1}. [{mem_type}] {content}"
        for i, (content, mem_type) in enumerate(candidates)
    )

    system_prompt = f"""你是记忆保存确认器。判断每条候选信息是否值得存入长期记忆。

值得保存:
- 可能在未来对话中用到的事实、偏好、决定
- 关于用户身份、职业、习惯的明确信息

不值得保存:
- 临时性质的闲聊（"我叫外卖"、"我喜欢这首歌"）
- 模糊不确定的表述
- 常识性内容

候选信息:
{items_text}

逐一判断，用 decide_memories 返回值得保存的编号列表。"""

    llm = create_llm()
    messages = [
        ChatMessage(role="system", content=system_prompt),
        ChatMessage(role="user", content="请判断以上候选信息"),
    ]

    tool_calls_acc = []
    async for chunk in llm.chat_stream(messages, tools=[BATCH_MEMORY_TOOL]):
        if chunk.tool_calls:
            tool_calls_acc = chunk.tool_calls

    if tool_calls_acc:
        tc = tool_calls_acc[0]
        if tc.name == "decide_memories":
            indices = tc.arguments.get("save_indices", [])
            if isinstance(indices, list):
                return [
                    candidates[i - 1]
                    for i in indices
                    if isinstance(i, int) and 1 <= i <= len(candidates)
                ]

    return []
