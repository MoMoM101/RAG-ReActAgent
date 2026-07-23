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
    assert classify_query("security") == QueryType.SHORT_KEYWORD
    assert classify_query("hello") == QueryType.SHORT_KEYWORD


def test_classify_natural_language():
    assert classify_query(
        "what parameters are required to create a payment order"
    ) == QueryType.NATURAL_LANGUAGE
    assert classify_query(
        "how do I configure a new user account"
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


class TestAmbiguousShort:
    def test_single_ambiguous_word(self):
        assert classify_query("refund") == QueryType.AMBIGUOUS_SHORT
        assert classify_query("encryption") == QueryType.AMBIGUOUS_SHORT
        assert classify_query("accuracy") == QueryType.AMBIGUOUS_SHORT

    def test_two_word_ambiguous(self):
        assert classify_query("deletion right") == QueryType.AMBIGUOUS_SHORT
        assert classify_query("breach notification") == QueryType.AMBIGUOUS_SHORT

    def test_three_word_ambiguous(self):
        assert classify_query("breach notification requirement") == QueryType.AMBIGUOUS_SHORT

    def test_short_with_entity_anchor_is_not_ambiguous(self):
        # "ERR_40003" has all-caps code → not ambiguous
        assert classify_query("ERR_40003") != QueryType.AMBIGUOUS_SHORT
        # "GDPR refund" has entity anchor
        assert classify_query("GDPR refund") != QueryType.AMBIGUOUS_SHORT

    def test_short_specific_entity_is_not_ambiguous(self):
        # Proper names are not ambiguous short
        result = classify_query("SHT45 sensor")
        assert result != QueryType.AMBIGUOUS_SHORT

    def test_common_non_concept_word_is_not_ambiguous(self):
        # "hello" is not an ambiguous concept
        assert classify_query("hello") != QueryType.AMBIGUOUS_SHORT
        assert classify_query("test") != QueryType.AMBIGUOUS_SHORT

    def test_ambiguous_short_profile_favors_keyword_heavily(self):
        profile = get_profile("refund")
        assert profile.keyword_weight >= 2.5
        assert profile.semantic_weight <= 0.8
        assert profile.keyword_weight > 3 * profile.semantic_weight


class TestAmbiguousCrossDomain:
    def test_concept_without_anchor(self):
        assert (
            classify_query("how to handle a refund when payment is not confirmed")
            == QueryType.AMBIGUOUS_CROSS_DOMAIN
        )
        assert (
            classify_query("what is the deletion right when consent withdrawn")
            == QueryType.AMBIGUOUS_CROSS_DOMAIN
        )

    def test_concept_with_anchor_is_not_ambiguous(self):
        # "GDPR" is an entity anchor
        result = classify_query("what is GDPR Art.17 deletion right")
        assert result != QueryType.AMBIGUOUS_CROSS_DOMAIN
        # "72 hours" is an entity anchor pattern
        result2 = classify_query("72 hours data breach notification")
        assert result2 != QueryType.AMBIGUOUS_CROSS_DOMAIN

    def test_no_concept_word_is_not_cross_domain(self):
        # "how are you" has no ambiguous concept words
        assert classify_query("how are you today") != QueryType.AMBIGUOUS_CROSS_DOMAIN

    def test_ambiguous_cross_domain_profile_favors_keyword(self):
        profile = DEFAULT_PROFILES[QueryType.AMBIGUOUS_CROSS_DOMAIN]
        assert profile.keyword_weight >= 2.0
        assert profile.keyword_weight > 2 * profile.semantic_weight
