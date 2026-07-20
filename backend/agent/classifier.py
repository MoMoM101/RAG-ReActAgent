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
    """快速规则匹配，命中返回 IntentHint，未命中返回 None。"""

    # Pure acknowledgment
    ack_words = {"好的", "嗯", "ok", "OK", "好", "行", "可以", "明白了", "懂了", "谢谢", "感谢"}
    if has_history and query.strip() in ack_words:
        return IntentHint(
            intent="acknowledgment", confidence=0.9, suggested_tools=[],
            hint_text="用户只是确认/致谢，不需要调用工具，简单回应即可。",
        )

    # Short / pronoun followup
    followup_markers = {
        "它", "他", "她", "这个", "那个", "这些", "那些",
        "这", "那", "哪个", "还有", "继续", "接着",
        "上面", "刚才", "之前", "呢",
    }
    is_short = len(query) <= 12
    has_followup_marker = any(m in query for m in followup_markers)
    if has_history and (has_followup_marker or is_short):
        return IntentHint(
            intent="context_followup", confidence=0.85, suggested_tools=["search_docs"],
            hint_text="这是一个追问。请用对话历史理解用户在指什么，将指代词替换为具体名词后调用 search_docs 检索。",
        )

    # Medium followup
    if has_history and len(query) <= 30:
        return IntentHint(
            intent="possible_followup", confidence=0.5, suggested_tools=["search_docs"],
            hint_text="用户可能在继续之前的话题。请结合对话历史补全query后调用 search_docs 检索。",
        )

    # Calculator
    calc_kw = {"计算", "算", "等于", "加", "减", "乘", "除", "+", "-", "*", "/"}
    if any(kw in query for kw in calc_kw) and re.search(r"[\d+\-*/]", query):
        return IntentHint(
                intent="calculation", confidence=0.7, suggested_tools=["calculator"],
                hint_text="用户可能在询问数学计算，建议使用 calculator 进行计算",
            )

    # Document listing
    if any(kw in query for kw in {"有哪些文档", "文档列表", "所有文档", "什么文档", "哪些文件", "文件列表", "列出文档"}):
        return IntentHint(
            intent="document_listing", confidence=0.7, suggested_tools=["list_documents"],
            hint_text="用户想查看知识库中的文档列表，建议使用 list_documents",
        )

    # Personal information — broad regex catches self-introductions the
    # regex intercept may miss (e.g. "本人是", "我的岗位是", "目前在从事")
    identity_patterns = [
        r"我(?:\S)?(?:职责|职业|工作|身份|岗位|职务|职位)\S*是",
        r"我(?:\S)?是(?:\S)?(?:一名|一个|一位)",
        r"本人(?:\S)?是",
        r"我的(?:工作|职业|岗位|职责|身份|职务|职位)",
    ]
    if any(re.search(p, query) for p in identity_patterns):
        return IntentHint(
            intent="personal_memory", confidence=0.85, suggested_tools=["recall_memory"],
            hint_text="用户在分享个人身份信息（职业/工作相关）。请确认已理解并适当回应，系统会自动保存这些信息。",
        )

    return None  # 规则未命中，走 LLM


INTENT_TOOL = {
    "type": "function",
    "function": {
        "name": "classify_intent",
        "description": "判断用户意图并推荐工具。如果用户透露了个人信息，同时提取可保存的内容。",
        "parameters": {
            "type": "object",
            "properties": {
                "intent": {
                    "type": "string",
                    "enum": ["personal_memory", "knowledge_retrieval", "web_search",
                             "document_info", "general_chat"],
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

    system_prompt = """你是意图分类器。根据用户消息判断意图并推荐工具。

意图类型:
- personal_memory: 用户透露个人信息（"我是/我叫/我职责/我职业/我工作/我身份/我喜欢/我习惯/我决定"）
  或询问记忆（"我是谁/我之前说过什么/还记得吗"）
  推荐: recall_memory
- knowledge_retrieval: 用户询问文档/知识问题（"什么是/如何/怎么/有哪些"）
  推荐: search_docs
- web_search: 用户想搜索互联网（"网上查/搜索一下/最新"）
  推荐: web_search
- document_info: 用户想了解文档详情（"文档信息/多少个切片"）
  推荐: get_document_info 或 list_documents
- general_chat: 闲聊、问候、或无需工具的简单问题
  推荐: []

规则:
- 用户在以任何方式介绍自己（名字、职业、身份、角色、工作、职责、偏好、习惯、决定）→ personal_memory
- "我是谁""我叫什么""还记得吗" → personal_memory
- 技术/知识类问题 → knowledge_retrieval
- 带"网上""搜索""最新""新闻" → web_search

重要: 即使用户的主要意图是 knowledge_retrieval 或 general_chat，
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
        hint_text="请判断用户意图。如果是新话题需要检索则调用 search_docs，能直接回答则直接回答",
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
