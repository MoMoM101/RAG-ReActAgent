"""Intent classifier — fast rules + LLM fallback."""

import re
from dataclasses import dataclass

from llm.base import ChatMessage


@dataclass
class IntentHint:
    intent: str
    confidence: float
    suggested_tools: list[str]
    hint_text: str
    save_to_profile: list[dict] | None = None  # 意图分类时提取的待保存信息


def _rule_match(query: str, has_history: bool) -> IntentHint | None:
    """Rule layer removed — model autonomously decides tools via ReAct reasoning."""
    return None


INTENT_TOOL = {
    "type": "function",
    "function": {
        "name": "classify_intent",
        "description": "判断用户意图（仅识别 personal_memory 或 general_chat），提取可保存的个人信息。",
        "parameters": {
            "type": "object",
            "properties": {
                "intent": {
                    "type": "string",
                    "enum": ["personal_memory", "general_chat"],
                    "description": "用户意图分类",
                },
                "suggested_tools": {
                    "type": "array", "items": {"type": "string"},
                    "description": "推荐的工具",
                },
                "hint_text": {
                    "type": "string",
                    "description": "给主 LLM 的提示",
                },
                "save_to_profile": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "content": {
                                "type": "string",
                                "description": "要保存的信息值，不含前缀。如名字'馍馍'、角色'AI开发工程师'",
                            },
                            "type": {
                                "type": "string",
                                "enum": ["identity_name", "identity_role", "preference", "decision", "fact"],
                            },
                        },
                        "required": ["content", "type"],
                    },
                    "description": (
                        "用户消息中值得保存的个人信息。identity_name=姓名/昵称, "
                        "identity_role=职业/身份/角色。无论 intent 是什么，只要检测到就提取。若无则填 []"
                    ),
                },
            },
            "required": ["intent", "suggested_tools", "hint_text", "save_to_profile"],
        },
    },
}


async def _llm_classify(query: str, has_history: bool) -> IntentHint:
    """LLM 意图分类（规则未命中时调用）。"""
    from llm.factory import create_llm

    system_prompt = """你是意图分类器。判断用户消息的意图类型，并提取个人信息。

意图类型:
- personal_memory: 用户透露个人信息或询问记忆
- general_chat: 闲聊、问候、或其他类型

规则:
- 用户在以任何方式介绍自己（名字、职业、身份、角色、工作、职责、偏好、习惯、决定）→ personal_memory
- "我是谁""我叫什么""还记得吗" → personal_memory
- 其他所有情况 → general_chat

重要: 即使用户的主要意图是 general_chat，
只要消息中包含个人身份/偏好/决定信息，就额外在 save_to_profile 字段中提取出来。
系统会自动保存这些信息供未来对话使用。

穷举提取规则 — 必须严格遵守:
1. 逐句检查用户消息，确保每个独立身份信息都被提取，不要遗漏
2. "我是X，Y工程师" 包含两层信息 —— 名字/昵称X 和 职业Y工程师，必须分别作为 identity_name 和 identity_role 提取
3. 提取完成后自检: 这条消息一共有几个个人信息? 每条都提取了吗?
4. 不要因为已经提取了一个就跳过另一个 —— 有 N 个就要返回 N 条
5. content 字段只填提取到的值本身（如"馍馍"、"AI开发工程师"），不要加"用户叫"/"用户是"等前缀"""

    messages = [
        ChatMessage(role="system", content=system_prompt),
        ChatMessage(role="user", content=query),
    ]

    llm = create_llm()
    tool_calls_acc = []
    async for chunk in llm.chat_stream(messages, tools=[INTENT_TOOL]):
        if chunk.tool_calls:
            tool_calls_acc = chunk.tool_calls

    if tool_calls_acc:
        tc = tool_calls_acc[0]
        if tc.name == "classify_intent":
            save_to_profile = tc.arguments.get("save_to_profile", None)
            return IntentHint(
                intent=tc.arguments.get("intent", "general_chat"),
                confidence=0.7,
                suggested_tools=tc.arguments.get("suggested_tools", []),
                hint_text=tc.arguments.get("hint_text", ""),
                save_to_profile=save_to_profile,
            )

    return IntentHint(
        intent="general_chat", confidence=0.3, suggested_tools=[],
        hint_text="根据问题类型自行选择合适的工具。知识类问题优先搜索知识库，不足时可用联网搜索。",
    )


def classify_intent(query: str, history: list[ChatMessage] | None = None) -> IntentHint:
    """混合分类器：规则优先，LLM 兜底。同步返回（LLM 在调用方异步执行）。"""
    has_history = history is not None and len(history) > 0
    rule_result = _rule_match(query, has_history)
    if rule_result:
        return rule_result
    # 规则未命中，标记走 LLM — 由调用方 async 执行
    return IntentHint(
        intent="_llm_needed",
        confidence=0.0,
        suggested_tools=[],
        hint_text="",
    )


async def llm_classify(query: str, history: list[ChatMessage] | None = None) -> IntentHint:
    """异步 LLM 分类（当 classify_intent 返回 _llm_needed 时调用）。

    即使规则命中 personal_memory，如果未提取到 save_to_profile，
    仍运行一次 LLM 以穷举提取所有身份信息。
    """
    has_history = history is not None and len(history) > 0
    hint = classify_intent(query, history)
    if hint.intent != "_llm_needed":
        if hint.intent == "personal_memory" and not hint.save_to_profile:
            llm_hint = await _llm_classify(query, has_history)
            if llm_hint.save_to_profile:
                hint.save_to_profile = llm_hint.save_to_profile
        return hint
    return await _llm_classify(query, has_history)
