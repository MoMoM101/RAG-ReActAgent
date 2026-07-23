"""memory/profile.py unit tests — pure functions and core logic."""

from datetime import UTC, datetime, timedelta

import pytest

from memory.profile import (
    MAX_FACTS,
    _evict_facts,
    _flatten,
    _parse_id,
    _score_fact,
    format_profile,
)


def make_fact(content: str, days_ago: int = 0, access_count: int = 0) -> dict:
    ts = (datetime.now(UTC) - timedelta(days=days_ago)).isoformat()
    return {"content": content, "source": "test", "ts": ts, "access_count": access_count}


class TestScoreFact:
    def test_recent_fact_scores_high(self):
        fact = make_fact("recent", days_ago=0)
        score = _score_fact(fact)
        assert score > 0.6  # Recent should be high

    def test_old_fact_scores_low(self):
        fact = make_fact("ancient", days_ago=364)
        score = _score_fact(fact)
        assert score < 0.3  # Nearly a year old

    def test_high_access_boosts_score(self):
        fact = make_fact("popular", days_ago=180, access_count=100)
        score = _score_fact(fact)
        assert score > 0.35  # Access count contributes 30%

    def test_missing_ts_defaults_to_365_days(self):
        fact = {"content": "no ts", "source": "test", "access_count": 0}
        score = _score_fact(fact)
        assert score == pytest.approx(0.0, abs=0.1)

    def test_invalid_ts_defaults_to_365_days(self):
        fact = {"content": "bad ts", "ts": "not-a-date", "access_count": 0}
        score = _score_fact(fact)
        assert score == pytest.approx(0.0, abs=0.1)

    def test_no_access_count_defaults_to_zero(self):
        fact = {"content": "no count", "source": "test", "ts": datetime.now(UTC).isoformat()}
        score = _score_fact(fact)
        assert score > 0.6  # Recent, access_count defaults to 0


class TestEvictFacts:
    def test_under_limit_keeps_all(self):
        facts = [make_fact(f"fact{i}", days_ago=i) for i in range(5)]
        result = _evict_facts(facts, 10)
        assert len(result) == 5

    def test_over_limit_drops_lowest_scored(self):
        facts = [make_fact(f"f{i}", days_ago=i) for i in range(35)]
        result = _evict_facts(facts, MAX_FACTS)
        assert len(result) == MAX_FACTS
        # Oldest facts (highest days_ago) should be evicted
        contents = {f["content"] for f in result}
        assert "f0" in contents  # Newest
        assert "f34" not in contents  # Oldest

    def test_recent_facts_kept_over_old(self):
        """New facts survive, old facts get evicted."""
        facts = [
            make_fact("recent", days_ago=1, access_count=0),
            make_fact("old", days_ago=350, access_count=0),
        ]
        result = _evict_facts(facts, 1)
        assert len(result) == 1
        assert result[0]["content"] == "recent"

    def test_high_access_overcomes_age(self):
        """High access count can save an old fact over a newer one."""
        facts = [
            make_fact("new_no_access", days_ago=1, access_count=0),
            make_fact("old_high_access", days_ago=300, access_count=200),
        ]
        result = _evict_facts(facts, 1)
        assert len(result) == 1
        # old_high_access should win due to access count
        assert result[0]["content"] == "old_high_access"


class TestFlatten:
    def test_empty_profile(self):
        assert _flatten({}) == []

    def test_name_and_role(self):
        profile = {"name": "张三", "role": "工程师"}
        result = _flatten(profile)
        assert "用户名叫张三" in result
        assert "用户是工程师" in result

    def test_preferences_and_decisions(self):
        profile = {"preferences": ["喜欢Python", "喜欢Vim"], "decisions": ["选FastAPI"]}
        result = _flatten(profile)
        assert "喜欢Python" in result
        assert "喜欢Vim" in result
        assert "选FastAPI" in result

    def test_facts_dict_format(self):
        profile = {"facts": [{"content": "项目A", "source": "session"}, {"content": "项目B", "source": "interceptor"}]}
        result = _flatten(profile)
        assert "项目A" in result
        assert "项目B" in result

    def test_facts_string_format(self):
        """Old format facts (plain strings) still work."""
        profile = {"facts": ["old fact 1", "old fact 2"]}
        result = _flatten(profile)
        assert "old fact 1" in result
        assert "old fact 2" in result


class TestParseId:
    def test_valid_name(self):
        assert _parse_id("name:0") == ("name", 0)

    def test_valid_preference(self):
        assert _parse_id("preference:3") == ("preferences", 3)

    def test_valid_fact(self):
        assert _parse_id("fact:10") == ("facts", 10)

    def test_no_colon_returns_none(self):
        assert _parse_id("invalid") is None

    def test_unknown_field_returns_none(self):
        assert _parse_id("unknown:0") is None

    def test_non_numeric_index_returns_none(self):
        assert _parse_id("fact:abc") is None


class TestFormatProfile:
    def test_empty_profile(self):
        assert format_profile({}) == ""

    def test_full_profile(self):
        profile = {
            "name": "张三",
            "role": "Python工程师",
            "preferences": ["喜欢自动挡车", "用VSCode"],
            "decisions": ["选择FastAPI"],
            "facts": [{"content": "在开发RAG系统"}, {"content": "团队有3人"}],
        }
        result = format_profile(profile)
        assert "张三" in result
        assert "Python工程师" in result
        assert "喜欢自动挡车" in result
        assert "选择FastAPI" in result
        assert "RAG系统" in result
        assert "## 用户画像" in result

    def test_partial_profile_no_header(self):
        profile = {"facts": []}
        result = format_profile(profile)
        assert result == ""

    def test_facts_truncated_to_10(self):
        """Only last 10 facts are shown in formatted output."""
        profile = {"facts": [{"content": f"fact{i}"} for i in range(15)]}
        result = format_profile(profile)
        # Should show the last 10 only
        assert "fact14" in result
        assert "fact0" not in result
