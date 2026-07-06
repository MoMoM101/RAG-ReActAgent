"""Session-end memory extraction — runs after conversation completes."""

import json
from datetime import UTC


async def extract_session_memories(conversation_id: str):
    """增量提取会话记忆 — 只提取未提取过的消息。"""
    try:
        from datetime import datetime

        from sqlalchemy import func, select, update

        from models.database import async_session
        from models.orm import Conversation, Message

        async with async_session() as session:
            # 获取 last_extracted_at
            conv_result = await session.execute(
                select(Conversation.last_extracted_at)
                .where(Conversation.id == conversation_id)
            )
            last_ts = conv_result.scalar_one_or_none()

            # 统计自上次提取后的新消息
            stmt = select(func.count(Message.id)).where(
                Message.conversation_id == conversation_id
            )
            if last_ts:
                stmt = stmt.where(Message.created_at > last_ts)
            new_count = (await session.execute(stmt)).scalar() or 0

            if new_count < 5:
                return  # 不足 5 条，跳过

            # 获取新消息用于提取
            msg_stmt = select(Message).where(
                Message.conversation_id == conversation_id
            ).order_by(Message.created_at.asc())
            if last_ts:
                msg_stmt = msg_stmt.where(Message.created_at > last_ts)
            messages = (await session.execute(msg_stmt)).scalars().all()

            if not messages:
                return

            conversation_text = "\n".join(
                f"[{m.role}] {m.content or '(tool)'}"[:200]
                for m in messages
            )

        # LLM 提取 → 提取成功后才更新 last_extracted_at
        extracted = await _extract_with_llm(conversation_text)
        if not extracted:
            return

        # 先更新时间戳，commit 后再写入画像（画像写入失败不影响提取进度）
        async with async_session() as session:
            await session.execute(
                update(Conversation)
                .where(Conversation.id == conversation_id)
                .values(last_extracted_at=datetime.now(UTC))
            )
            await session.commit()

        from memory.profile import handle_session_extract
        await handle_session_extract(extracted)

    except Exception:
        import traceback
        traceback.print_exc()


async def _extract_with_llm(conversation_text: str) -> list[dict]:
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
- **只提取标注为 [user] 的消息中的信息，忽略 [assistant] 和 [tool] 消息**

记忆类型 (memory_type):
- identity: 用户身份信息（姓名、职业、角色）
- preference: 用户偏好（喜欢什么、不喜欢什么）
- decision: 用户做的决定（技术选型、方案选择）
- fact: 与用户相关的事实（项目名、在做的事）

输出格式:
[
  {"content": "用户是Python开发者", "memory_type": "identity"},
  {"content": "用户决定用FastAPI做后端", "memory_type": "decision"}
]

如果对话中没有用户个人信息，返回空数组 []。
只返回 JSON 数组，不要其他文字。"""

    llm = create_llm()
    # 按消息行截断，而非硬切字符：取最近 40 条消息行
    lines = conversation_text.split("\n")
    truncated = "\n".join(lines[-40:]) if len(lines) > 40 else conversation_text
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
        return []
    except json.JSONDecodeError:
        return []
