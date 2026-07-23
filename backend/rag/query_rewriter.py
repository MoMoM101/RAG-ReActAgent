"""Query rewriting for multi-variant parallel search."""

import logging

from llm.base import ChatMessage
from llm.factory import create_llm

logger = logging.getLogger(__name__)

REWRITE_SYSTEM_PROMPT = """你是查询改写器。将用户查询改写为更适合搜索的短关键词。
规则：
- 每个改写版本独立一行，不含编号
- 包含原查询的核心语义，补充同义词和相关术语
- 不要扩展为完整句子，保持关键词风格
- 每个版本角度不同（如：中英文、同义词、上位概念）"""


async def rewrite(query: str, n_variants: int = 2) -> list[str]:
    """Produce n search-optimized keyword variants via LLM.

    Returns empty list on failure — caller should fall back to original query only.
    """
    if n_variants <= 0:
        return []

    try:
        llm = create_llm()
        messages = [
            ChatMessage(role="system", content=REWRITE_SYSTEM_PROMPT),
            ChatMessage(
                role="user",
                content=f"改写为 {n_variants} 个搜索版本：\n{query}",
            ),
        ]
        parts: list[str] = []
        async for chunk in llm.chat_stream(messages):
            if chunk.content:
                parts.append(chunk.content)

        raw = "".join(parts).strip()
        variants = [line.strip() for line in raw.split("\n") if line.strip()]
        variants = [v for v in variants[:n_variants] if v and len(v) >= 2]
        if variants:
            logger.debug("query rewrite: %r → variants=%s", query, variants)
        return variants
    except Exception:
        logger.warning("query rewrite failed, falling back to original", exc_info=True)
        return []
