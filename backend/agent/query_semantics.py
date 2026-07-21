"""Deterministic query semantics shared by planning and grounding guards."""

from __future__ import annotations

import re

from llm.base import ChatMessage

COMPARISON_QUERY_RE = re.compile(
    r"不同|区别|差异|相同|相似|比较|对比|相比|相较|共同点|优缺点|"
    r"哪个更|哪个好|怎么选|如何选|选哪个|各自|各适用|"
    r"\bvs\.?\b|\bversus\b|\bcompare(?:d|s|ing)?\b|\bcomparison\b",
    re.IGNORECASE,
)
UNRESOLVED_REFERENCE_RE = re.compile(
    r"它们|两者|前者|后者|它(?!们)|这个|那个|上述|刚才(?:说|提到)的|前面(?:说|提到)的",
)
COVERAGE_QUERY_RE = re.compile(r"^(?:什么是|有哪些|概览|详细说明|介绍一下|说说|讲讲)")
RELATION_SENSITIVE_QUERY_RE = re.compile(
    r"职责|作用分别|原因|为什么|为何|如何影响|有什么影响|什么关系|有何关系|"
    r"怎么计算|如何计算|计算公式|公式是什么|"
    r"哪(?:个|种|项|类).{0,10}最|最(?:能|适合|有效|好|优|佳)|最佳|最好|首选",
)
UNDERSPECIFIED_QUERY_RE = re.compile(
    r"^\s*(?:怎么做|如何做|有哪些方法|有什么方法|介绍一下|详细说说|展开说说|继续说)\s*[？?]?\s*$",
)

_SINGULAR_REFERENCE_RE = re.compile(r"这个|那个|它(?!们)")
_PLURAL_REFERENCE_RE = re.compile(r"它们|两者|前者|后者")
_LEADING_COMPARISON_RE = re.compile(r"^\s*(?:和|与|跟|同)\s*\S+")
_SIMPLE_TOPIC_PATTERNS = (
    re.compile(
        r"^\s*(?P<topic>[A-Za-z][A-Za-z0-9_.+\- ]{0,30}|[\u4e00-\u9fffA-Za-z0-9_.+\-]{2,24})"
        r"(?:是什么|指什么|是啥|的定义|有哪些功能)[？?]?\s*$",
        re.IGNORECASE,
    ),
    re.compile(
        r"^\s*(?:请)?(?:介绍|说说|讲讲|解释)(?:一下)?\s*"
        r"(?P<topic>[A-Za-z][A-Za-z0-9_.+\- ]{0,30}|[\u4e00-\u9fffA-Za-z0-9_.+\-]{2,24})[？?]?\s*$",
        re.IGNORECASE,
    ),
)
_PAIR_CONNECTOR_RE = re.compile(
    r"\s*(?:和|与|跟|同|相比(?:于)?|相较(?:于)?|\bvs\.?\b|\bversus\b)\s*",
    re.IGNORECASE,
)
_PAIR_LEAD_RE = re.compile(r"^\s*(?:请)?(?:比较|对比|说说|讲讲|分析)?\s*", re.IGNORECASE)
_PAIR_TAIL_RE = re.compile(
    r"\s*(?:有什么|有何|存在哪些|的)?(?:不同|区别|差异|共同点|优缺点|联系|关系)"
    r"|\s*(?:各自)?(?:有什么|有何)(?:优势|劣势|特点|功能|作用|用途|适用场景).*$"
    r"|\s*(?:各自)?适合.*$"
    r"|\s*(?:在什么|做什么|用于什么|哪个|谁|怎么|如何|是否).*$",
    re.IGNORECASE,
)
_HISTORY_CITATION_RE = re.compile(r"\s*\[S\d+(?:\s*[,，]\s*S\d+)*\]", re.IGNORECASE)


def is_comparison_query(query: str) -> bool:
    return bool(COMPARISON_QUERY_RE.search(query))


def has_unresolved_reference(query: str) -> bool:
    return bool(UNRESOLVED_REFERENCE_RE.search(query))


def is_coverage_query(query: str) -> bool:
    return bool(COVERAGE_QUERY_RE.search(query.strip()))


def is_underspecified_query(query: str) -> bool:
    return bool(UNDERSPECIFIED_QUERY_RE.fullmatch(query))


def requires_whole_answer_validation(query: str) -> bool:
    """Return whether a query must be validated only after full generation."""
    return bool(
        is_comparison_query(query)
        or is_coverage_query(query)
        or has_unresolved_reference(query)
        or RELATION_SENSITIVE_QUERY_RE.search(query)
    )


def extract_comparison_entities(query: str) -> tuple[str, str] | None:
    """Extract the two explicit sides of a comparison when safely possible."""
    text = _PAIR_LEAD_RE.sub("", query.strip().strip("？?。"))
    match = _PAIR_CONNECTOR_RE.search(text)
    if not match:
        return None
    left = text[: match.start()].strip(" ，,：:")
    right = text[match.end() :].strip(" ，,：:")
    right = _PAIR_TAIL_RE.split(right, maxsplit=1)[0].strip(" ，,：:")
    if not left or not right or len(left) > 40 or len(right) > 40:
        return None
    if has_unresolved_reference(left) or has_unresolved_reference(right):
        return None
    return left, right


def _simple_topic(text: str) -> str | None:
    for pattern in _SIMPLE_TOPIC_PATTERNS:
        match = pattern.match(text)
        if match:
            topic = match.group("topic").strip()
            if topic and not has_unresolved_reference(topic):
                return topic
    return None


def resolve_followup_query(
    user_message: str,
    conversation_history: list[ChatMessage],
) -> str:
    """Resolve safe singular/plural references from recent explicit topics."""
    needs_topic = is_underspecified_query(user_message)
    needs_comparison_subject = bool(_LEADING_COMPARISON_RE.match(user_message))
    if not has_unresolved_reference(user_message) and not needs_topic and not needs_comparison_subject:
        return user_message

    for message in reversed(conversation_history):
        if message.role != "user" or not message.content:
            continue
        pair = extract_comparison_entities(message.content)
        if pair and _PLURAL_REFERENCE_RE.search(user_message):
            first, second = pair

            def replace_plural(
                match: re.Match[str],
                first_entity: str = first,
                second_entity: str = second,
            ) -> str:
                if match.group() == "前者":
                    return first_entity
                if match.group() == "后者":
                    return second_entity
                return f"{first_entity}和{second_entity}"

            return _PLURAL_REFERENCE_RE.sub(
                replace_plural,
                user_message,
            )
        topic = _simple_topic(message.content)
        if topic and needs_comparison_subject:
            return f"{topic}{user_message.lstrip()}"
        if topic and needs_topic:
            return f"{topic}的详细说明"
        if topic and _SINGULAR_REFERENCE_RE.search(user_message):
            return _SINGULAR_REFERENCE_RE.sub(topic, user_message)
    return user_message


def sanitize_conversation_history(
    conversation_history: list[ChatMessage],
) -> list[ChatMessage]:
    """Keep conversational context while removing stale tool evidence/citations."""
    sanitized: list[ChatMessage] = []
    for message in conversation_history:
        if message.role not in {"user", "assistant"} or not message.content:
            continue
        content = message.content
        if message.role == "assistant":
            content = _HISTORY_CITATION_RE.sub("", content)
        sanitized.append(ChatMessage(role=message.role, content=content))
    return sanitized
