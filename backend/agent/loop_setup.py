"""Intent and memory preparation for an agent turn."""

import asyncio
import logging

from agent.classifier import IntentHint, classify_intent, llm_classify
from config import settings
from llm.base import ChatMessage

logger = logging.getLogger(__name__)


async def classify_turn(
    user_message: str,
    conversation_history: list[ChatMessage],
) -> IntentHint:
    """Classify intent with the existing rule-first, LLM-fallback policy."""
    hint = classify_intent(user_message, conversation_history)
    if hint.intent == "_llm_needed" or (hint.intent == "personal_memory" and not hint.save_to_profile):
        try:
            hint = await asyncio.wait_for(
                llm_classify(user_message, conversation_history),
                timeout=settings.rag_timeout_intent,
            )
        except TimeoutError:
            logger.warning("intent classification timed out, defaulting to knowledge_qa")
            hint = classify_intent(user_message, conversation_history)
            hint.intent = "knowledge_qa"
    return hint


async def apply_memory_context(
    user_message: str,
    hint: IntentHint,
) -> tuple[IntentHint, list[str]]:
    """Persist detected memories and enrich personal-memory turns."""
    from agent.intercept import confirm_candidates_batch, extract_memory_candidates
    from memory.profile import handle_intercept

    regex_candidates = extract_memory_candidates(user_message)
    saved: list[str] = []
    identity_direct: list[tuple[str, str]] = []
    needs_confirmation: list[tuple[str, str]] = []
    for content, memory_type in regex_candidates:
        target = identity_direct if memory_type == "identity" else needs_confirmation
        target.append((content, memory_type))

    for item in hint.save_to_profile or []:
        content = item.get("content", "")
        memory_type = item.get("type", "fact")
        candidate_pair = (content, memory_type)
        if content and candidate_pair not in identity_direct and candidate_pair not in needs_confirmation:
            identity_direct.append(candidate_pair)

    logger.info(
        "memory intercept: regex=%d (identity=%d need_confirm=%d) classifier_save=%d user_msg=%.60s",
        len(regex_candidates),
        len(identity_direct),
        len(needs_confirmation),
        len(hint.save_to_profile or []),
        user_message,
    )

    for candidate, memory_type in identity_direct:
        try:
            await handle_intercept(candidate, memory_type)
            saved.append(candidate)
        except Exception:
            logger.error(
                "memory intercept save failed candidate=%s type=%s",
                candidate,
                memory_type,
                exc_info=True,
            )

    if needs_confirmation:
        try:
            confirmed = await confirm_candidates_batch(needs_confirmation)
            for candidate, memory_type in confirmed:
                await handle_intercept(candidate, memory_type)
                saved.append(candidate)
        except Exception:
            logger.error("memory intercept confirm failed", exc_info=True)

    if saved:
        hint.hint_text = f"[系统] 已记录: {'; '.join(saved)}\n" + hint.hint_text
        logger.info("memory intercept saved=%d items=%s", len(saved), saved)
    else:
        logger.info("memory intercept: nothing saved")

    if hint.intent == "personal_memory" and "recall_memory" in hint.suggested_tools:
        from memory.profile import search_profile

        recalled = await search_profile(user_message, top_k=5)
        if recalled:
            recall_text = "\n".join(f"- {item['text']}" for item in recalled)
            hint.hint_text = f"[系统] 记忆检索结果:\n{recall_text}\n" + hint.hint_text

    return hint, saved
