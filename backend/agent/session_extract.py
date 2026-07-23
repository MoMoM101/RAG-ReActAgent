"""Session-end memory extraction — runs after conversation completes."""

import asyncio
import json
import logging

logger = logging.getLogger(__name__)
_EXTRACTION_LOCKS = tuple(asyncio.Lock() for _ in range(32))


def _truncate_recent_lines(text: str, max_tokens: int) -> str:
    """Keep complete recent lines within the configured tokenizer budget."""
    from agent.token_counter import get_token_counter
    from config import settings

    counter = get_token_counter(
        settings.llm_model,
        settings.tokenizer_provider,
        settings.tokenizer_model,
        settings.tokenizer_fallback_safety_factor,
    )
    selected: list[str] = []
    remaining = max(1, max_tokens)
    for line in reversed(text.splitlines()):
        line_tokens = counter.count_text(line + "\n")
        if line_tokens > remaining:
            if not selected:
                selected.append(counter.truncate_text(line, remaining))
            break
        selected.append(line)
        remaining -= line_tokens
    return "\n".join(reversed(selected))


async def extract_session_memories(conversation_id: str):
    """增量提取会话记忆，成功消费后按消息时间水位推进游标。"""
    lock = _EXTRACTION_LOCKS[hash(conversation_id) % len(_EXTRACTION_LOCKS)]
    async with lock:
        await _extract_session_memories_locked(conversation_id)


async def _extract_session_memories_locked(conversation_id: str) -> None:
    try:
        from sqlalchemy import or_, select, update

        from models.database import session_scope
        from models.orm import Conversation, Message

        async with session_scope() as session:
            # 获取 last_extracted_at
            conv_result = await session.execute(
                select(Conversation.last_extracted_at)
                .where(Conversation.id == conversation_id)
            )
            last_ts = conv_result.scalar_one_or_none()

            # 只读用户与助手消息；工具原始结果不属于用户画像。
            msg_stmt = select(Message).where(
                Message.conversation_id == conversation_id,
                Message.role.in_(("user", "assistant")),
            ).order_by(Message.created_at.asc(), Message.id.asc())
            if last_ts:
                msg_stmt = msg_stmt.where(Message.created_at > last_ts)
            messages = (await session.execute(msg_stmt)).scalars().all()

            if not messages:
                from metrics import get_metrics

                get_metrics().record_memory_extraction("no_messages")
                return

            # 精确水位：不能使用“提取结束时间”，否则会跳过提取期间新写入的消息。
            watermark = max(message.created_at for message in messages)

            conversation_text = "\n".join(
                f"[{m.role}] {m.content or '(tool)'}"[:200]
                for m in messages
            )

        # None 表示解析/调用失败，保留游标以便重试；[] 是有效空结果，也应消费。
        extracted = await _extract_with_llm(conversation_text)
        if extracted is None:
            from metrics import get_metrics

            get_metrics().record_memory_extraction("retryable_failure")
            return

        if extracted:
            from memory.profile import handle_session_extract

            await handle_session_extract(extracted)

        # 画像写入成功（或有效空结果）后才推进精确消息水位。
        async with session_scope() as session:
            await session.execute(
                update(Conversation)
                .where(
                    Conversation.id == conversation_id,
                    or_(
                        Conversation.last_extracted_at.is_(None),
                        Conversation.last_extracted_at < watermark,
                    ),
                )
                .values(last_extracted_at=watermark)
            )
            await session.commit()

        from metrics import get_metrics

        get_metrics().record_memory_extraction("saved" if extracted else "empty")

    except Exception:
        from metrics import get_metrics

        get_metrics().record_memory_extraction("failed")
        logger.warning(
            "session memory extraction failed conv_id=%s",
            conversation_id,
            exc_info=True,
        )


async def _extract_with_llm(conversation_text: str) -> list[dict] | None:
    """用 LLM 从对话文本中提取结构化记忆。"""
    from llm.base import ChatMessage
    from llm.factory import create_llm

    system_prompt = """你是记忆提取器。从对话中提取**仅用户**的个人信息，以 JSON 数组返回。

提取规则:
- 只提取用户透露的个人信息（身份、偏好、决定、项目等）
- 每条信息必须简洁准确
- 不要提取临时性闲聊内容
- 不要重复已有信息
- **不要提取 AI 助手回答中的知识/事实内容**（如技术定义、文档摘要等）
- **不要提取知识库操作行为**（如"用户上传了某文档"、"用户在知识库中放置了某文件"等）
- **只提取标注为 [user] 的消息中的信息，忽略 [assistant] 和 [tool] 消息**
- **如果用户同时透露了名字和职业，必须分别提取为 identity_name 和 identity_role，不要合并**
- **content 字段只填提取到的值本身（如"馍馍"），不要加"用户叫"/"用户是"等前缀**

记忆类型 (memory_type):
- identity_name: 用户姓名或昵称
- identity_role: 用户的职业、身份、角色
- preference: 用户偏好（喜欢什么、不喜欢什么）
- decision: 用户做的决定（技术选型、方案选择）
- fact: 与用户相关的事实（项目名、在做的事）

输出格式:
[
  {"content": "张三", "memory_type": "identity_name"},
  {"content": "Python开发者", "memory_type": "identity_role"},
  {"content": "决定用FastAPI做后端", "memory_type": "decision"}
]

如果对话中没有用户个人信息，返回空数组 []。
只返回 JSON 数组，不要其他文字。"""

    llm = create_llm()
    # 按 tokenizer 预算保留最近的完整消息行，避免字符估算偏差和半条消息。
    from config import settings

    truncated = _truncate_recent_lines(
        conversation_text,
        settings.memory_extract_max_tokens,
    )
    messages = [
        ChatMessage(role="system", content=system_prompt),
        ChatMessage(role="user", content=truncated),
    ]

    content_parts = []
    async for chunk in llm.chat_stream(messages):
        if chunk.content:
            content_parts.append(chunk.content)

    raw = "".join(content_parts).strip()

    # 提取 JSON 数组
    if "```" in raw:
        raw = raw.split("```")[1]
        if raw.startswith("json"):
            raw = raw[4:]
        raw = raw.strip()

    # 找到 JSON 数组起止位置
    start = raw.find("[")
    end = raw.rfind("]")
    if start >= 0 and end > start:
        raw = raw[start:end + 1]

    try:
        parsed = json.loads(raw)
        if isinstance(parsed, dict):
            return [parsed]  # LLM 输出了单个对象
        if isinstance(parsed, list):
            return parsed
        return None
    except json.JSONDecodeError:
        return None
