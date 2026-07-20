"""Pure user-profile scoring, parsing, flattening, and formatting helpers."""

from datetime import UTC, datetime

MAX_FACTS = 30

_FIELD_MAP = {
    "name": "name",
    "role": "role",
    "preference": "preferences",
    "decision": "decisions",
    "fact": "facts",
}


def score_fact(fact: dict) -> float:
    ts_str = fact.get("ts", "")
    days = 365.0
    if ts_str:
        try:
            value = datetime.fromisoformat(ts_str)
            days = (datetime.now(UTC) - value).total_seconds() / 86400.0
        except (ValueError, TypeError):
            pass
    recency_score = max(0.0, 1.0 - days / 365.0)
    access_count = fact.get("access_count", 0)
    return access_count * 0.3 + recency_score * 0.7


def evict_facts(facts: list[dict], max_count: int) -> list[dict]:
    if len(facts) <= max_count:
        return facts
    scored = [(fact, score_fact(fact)) for fact in facts]
    scored.sort(key=lambda item: item[1], reverse=True)
    return [fact for fact, _ in scored[:max_count]]


def empty_profile() -> dict:
    return {"name": "", "role": "", "preferences": [], "decisions": [], "facts": []}


def flatten_profile(data: dict) -> list[str]:
    texts = []
    if data.get("name"):
        texts.append(f"用户名叫{data['name']}")
    if data.get("role"):
        texts.append(f"用户是{data['role']}")
    texts.extend(data.get("preferences", []))
    texts.extend(data.get("decisions", []))
    for fact in data.get("facts", []):
        texts.append(fact["content"] if isinstance(fact, dict) else fact)
    return texts


def parse_item_id(item_id: str) -> tuple[str, int] | None:
    parts = item_id.split(":", 1)
    if len(parts) != 2:
        return None
    field = _FIELD_MAP.get(parts[0])
    if field is None:
        return None
    try:
        return field, int(parts[1])
    except ValueError:
        return None


def format_profile_text(profile: dict) -> str:
    if not profile:
        return ""
    parts = []
    if profile.get("name"):
        parts.append(f"用户名: {profile['name']}")
    if profile.get("role"):
        parts.append(f"职业: {profile['role']}")
    preferences = profile.get("preferences", [])
    if preferences:
        parts.append(f"偏好: {'、'.join(preferences)}")
    decisions = profile.get("decisions", [])
    if decisions:
        parts.append(f"已知决策: {'、'.join(decisions)}")
    facts = profile.get("facts", [])
    if facts:
        flat = [fact["content"] if isinstance(fact, dict) else fact for fact in facts]
        parts.append(f"补充信息: {'、'.join(flat[-10:])}")
    if not parts:
        return ""
    return "## 用户画像\n" + "\n".join(parts) + "\n"
