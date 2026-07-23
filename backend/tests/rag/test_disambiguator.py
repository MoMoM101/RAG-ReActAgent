"""Tests for disambiguation signal extraction and scoring."""

from rag.disambiguator import (
    DisambigSignals,
    apply_signal_bonus,
    count_signal_hits,
    extract_disambiguation_signals,
)


class DummyResult:
    def __init__(self, score, text, doc_id="", chunk_id=""):
        self.score = score
        self.text = text
        self.document_id = doc_id
        self.chunk_id = chunk_id


class TestExtractDisambiguationSignals:
    def test_extracts_currency_hints(self):
        signals = extract_disambiguation_signals("20 million EUR fine")
        assert any("EUR" in c or "20" in c for c in signals.currency_hints)

    def test_extracts_currency_cny(self):
        signals = extract_disambiguation_signals("50 million CNY fine for violation")
        assert any("CNY" in c or "50" in c for c in signals.currency_hints)

    def test_extracts_number_constraints(self):
        signals = extract_disambiguation_signals("72 hours data breach notification")
        assert any("72" in n and "hours" in n for n in signals.number_constraints)

    def test_extracts_temperature_spec(self):
        signals = extract_disambiguation_signals("-40C to 125C industrial sensor")
        # "-40C" or "125C" should be extracted (pattern matches bare C for temp)
        assert any("40C" in n or "125C" in n or "C" in n for n in signals.number_constraints)

    def test_extracts_hex_address(self):
        signals = extract_disambiguation_signals("I2C address 0x76 sensor")
        assert any("0x76" in n for n in signals.number_constraints)

    def test_extracts_entity_names(self):
        signals = extract_disambiguation_signals("SHT45 sensor with PTFE membrane")
        assert any("SHT45" in e for e in signals.entity_names)

    def test_extracts_multiple_entities(self):
        signals = extract_disambiguation_signals("BME280 vs HDC3020 comparison")
        entity_names_lower = [e.lower() for e in signals.entity_names]
        assert "bme280" in entity_names_lower
        assert "hdc3020" in entity_names_lower

    def test_extracts_standard_refs(self):
        signals = extract_disambiguation_signals("GDPR Article 17 deletion right")
        assert any("GDPR" in s for s in signals.standard_refs)
        assert any("Art" in s or "Article" in s for s in signals.standard_refs)

    def test_extracts_pci_dss(self):
        signals = extract_disambiguation_signals("PCI DSS PAN masking requirements")
        assert any("PCI" in s for s in signals.standard_refs)

    def test_extracts_exclusion_signals(self):
        signals = extract_disambiguation_signals(
            "refund handling unlike the Stripe API flow"
        )
        # exclusion tokens capture the negated concept
        assert signals.exclusion_tokens

    def test_no_signals_for_plain_query(self):
        signals = extract_disambiguation_signals("how are you today")
        assert not signals.has_signals()

    def test_has_signals_true_when_any_present(self):
        signals = extract_disambiguation_signals("GDPR compliance requirements")
        assert signals.has_signals()

    def test_empty_query(self):
        signals = extract_disambiguation_signals("")
        assert not signals.has_signals()
        assert signals.all_tokens() == []


class TestCountSignalHits:
    def test_counts_matching_tokens(self):
        signals = DisambigSignals(
            entity_names=["GDPR"],
            standard_refs=["Art.17"],
            currency_hints=["20M EUR"],
        )
        text = "Under GDPR Art.17, individuals have the right to erasure."
        hits = count_signal_hits(text, signals)
        assert hits >= 2  # GDPR + Art.17 match

    def test_zero_hits_when_none_match(self):
        signals = DisambigSignals(
            entity_names=["GDPR"],
            currency_hints=["CNY"],
        )
        text = "PCI DSS requires PAN masking."
        hits = count_signal_hits(text, signals)
        assert hits == 0

    def test_case_insensitive_match(self):
        signals = DisambigSignals(entity_names=["GDPR"])
        hits = count_signal_hits("gdpr compliance document", signals)
        assert hits == 1


class TestApplySignalBonus:
    def test_boosts_results_with_more_signals(self):
        signals = DisambigSignals(entity_names=["GDPR"], standard_refs=["Art.17"])
        results = [
            DummyResult(score=0.9, text="GDPR Art.17 grants erasure rights"),
            DummyResult(score=0.9, text="PIPL Article 47 covers deletion"),
            DummyResult(score=0.8, text="PCI DSS section 3.3 PAN masking"),
        ]
        apply_signal_bonus(results, signals)
        # GDPR result should be boosted above PIPL
        assert results[0].text.startswith("GDPR")

    def test_no_signals_returns_unchanged(self):
        signals = DisambigSignals()
        results = [
            DummyResult(score=0.9, text="alpha"),
            DummyResult(score=0.5, text="beta"),
        ]
        original_order = [r.text for r in results]
        apply_signal_bonus(results, signals)
        assert [r.text for r in results] == original_order

    def test_no_results(self):
        signals = DisambigSignals(entity_names=["GDPR"])
        results = []
        apply_signal_bonus(results, signals)
        assert results == []

    def test_single_result(self):
        signals = DisambigSignals(entity_names=["GDPR"])
        results = [DummyResult(score=1.0, text="GDPR compliance")]
        apply_signal_bonus(results, signals)
        assert len(results) == 1
        assert results[0].score > 1.0  # bonus applied

    def test_multiple_hits_compound(self):
        signals = DisambigSignals(
            entity_names=["BME280"],
            number_constraints=["0x76"],
            standard_refs=["I2C"],
        )
        results = [
            DummyResult(score=0.8, text="BME280 sensor I2C 0x76 specs"),
            DummyResult(score=0.8, text="SHT45 sensor specs"),
        ]
        apply_signal_bonus(results, signals)
        # BME280 with 3 hits should be boosted above SHT45
        assert results[0].score > results[1].score
        # 3 hits × 0.08 = +24% bonus → 0.8 * 1.24 ≈ 0.992
        assert results[0].score > 0.95
