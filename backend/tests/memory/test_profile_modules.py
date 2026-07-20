import memory.profile as facade
from memory.profile_core import (
    empty_profile,
    evict_facts,
    flatten_profile,
    format_profile_text,
    parse_item_id,
    score_fact,
)
from memory.profile_store import load_profile, save_profile


def test_profile_facade_preserves_pure_helper_symbols():
    assert facade._empty is empty_profile
    assert facade._evict_facts is evict_facts
    assert facade._flatten is flatten_profile
    assert facade.format_profile is format_profile_text
    assert facade._parse_id is parse_item_id
    assert facade._score_fact is score_fact


async def test_profile_store_creates_and_updates_latest_profile():
    assert await load_profile() == empty_profile()

    first = {
        "name": "Alice",
        "role": "Engineer",
        "preferences": [],
        "decisions": [],
        "facts": [],
    }
    await save_profile(first)
    assert await load_profile() == first

    second = {**first, "role": "Architect"}
    await save_profile(second)
    assert await load_profile() == second
