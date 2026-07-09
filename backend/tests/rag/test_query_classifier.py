"""Tests for query classifier used in adaptive RRF weighting."""
from rag.query_classifier import (
    DEFAULT_PROFILES,
    QueryProfile,
    QueryType,
    classify_query,
    get_profile,
)


def test_classify_error_code():
    assert classify_query("ERR_40003") == QueryType.EXACT_CODE
    assert classify_query("ERR_40401") == QueryType.EXACT_CODE
    assert classify_query("ERR_40201") == QueryType.EXACT_CODE


def test_classify_sku():
    assert classify_query("STM32H743VI") == QueryType.EXACT_CODE
    assert classify_query("R7FA6M5BH3CFC") == QueryType.EXACT_CODE
    # ESP32-S3R8 is a single-word SKU with dash — needs special pattern
    assert classify_query("ESP32-S3R8 price and stock") == QueryType.MIXED


def test_classify_numeric_spec():
    assert classify_query("ADC 5MSPS microcontroller") == QueryType.NUMERIC_SPEC
    assert classify_query("80mg max dose") == QueryType.NUMERIC_SPEC
    # Drug name + dosage is classified as drug lookup (drug check runs first)
    assert classify_query("80mg max dose atorvastatin") == QueryType.DRUG_LOOKUP
    assert classify_query("use 3.3V power supply") == QueryType.NUMERIC_SPEC


def test_classify_numeric_range():
    assert classify_query("-40C to 125C industrial MCU") == QueryType.NUMERIC_SPEC


def test_classify_clause():
    assert classify_query("clause 32 breach report deadline") == QueryType.CLAUSE_LOOKUP
    assert classify_query("clause 26 database password rotation") == QueryType.CLAUSE_LOOKUP


def test_classify_drug_lookup():
    assert classify_query("Clopidogrel loading dose") == QueryType.DRUG_LOOKUP
    assert classify_query("Atorvastatin drug interactions") == QueryType.DRUG_LOOKUP
    assert classify_query("Simvastatin 20mg") == QueryType.DRUG_LOOKUP


def test_classify_short_keyword():
    assert classify_query("encryption") == QueryType.SHORT_KEYWORD
    assert classify_query("security") == QueryType.SHORT_KEYWORD


def test_classify_natural_language():
    assert classify_query(
        "what parameters are required to create a payment order"
    ) == QueryType.NATURAL_LANGUAGE
    assert classify_query(
        "how soon must users be notified after a data breach"
    ) == QueryType.NATURAL_LANGUAGE


def test_classify_mixed():
    assert classify_query(
        "STM32 MCU with ETH ethernet interface"
    ) == QueryType.MIXED


def test_get_profile_returns_query_profile():
    profile = get_profile("ERR_40003")
    assert isinstance(profile, QueryProfile)
    assert profile.keyword_weight > profile.semantic_weight


def test_get_profile_natural_language_favors_semantic():
    profile = get_profile("what parameters are required")
    assert profile.semantic_weight > profile.keyword_weight


def test_all_query_types_have_profiles():
    for qtype in QueryType:
        assert qtype in DEFAULT_PROFILES, f"Missing profile for {qtype}"
