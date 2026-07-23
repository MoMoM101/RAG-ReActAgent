"""Entity constraint extraction for disambiguation in RAG retrieval.

Extracts hard constraint tokens from queries (currency units, numeric specs,
entity names, standard references) and uses them to re-weight retrieval
results — chunks that contain more of these signals get a score bonus.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

# ── Signal extraction patterns ────────────────────────────────────

_CURRENCY_PAT = re.compile(
    r"\b(\d[\d.,]*\s*(?:million|billion|EUR|CNY|USD|GBP|JPY|€|¥|\$|元|万元|亿元))\b",
    re.IGNORECASE,
)
_NUMBER_SPEC_PAT = re.compile(
    r"\b(-?\d+(?:\.\d+)?\s*(?:hours?|days?|h\b|°?C\b|°F|MHz|GHz|kHz|bps|Mbps|Gbps|"
    r"ms|µs|ppm|mm|cm|km|L|ml|mg|g|kg|mcg|V|A|W|kV|mA|mW|KB|MB|GB|TB|bit))\b"
    r"|\b(0x[0-9a-fA-F]+)\b"  # hex addresses like 0x76
    r"|\b(I2C|SPI|UART|GPIO)\b",  # interface names
    re.IGNORECASE,
)
_ENTITY_PAT = re.compile(
    r"\b([A-Z][A-Z0-9]{2,}(?:[_-][A-Z0-9]+)?)\b"   # SHT45, BME280, HDC3020
    r"|\b([A-Z][a-z]+(?:statin|sartan|prazole|dipine|olol|pril|mab|nib|"
    r"ciclovir|cycline|floxacin|azepam|caine|tidine|trel|grel|taxel|platin|"
    r"zomab|ximab|umab|tinib|rafenib|parib|gliptin|gliflozin|glutide|sertib))\b",
)
_STANDARD_PAT = re.compile(
    r"\b(GDPR|PIPL|PCI DSS|HIPAA|SOX|ISO\s*\d+|NIST|FIPS|FedRAMP|"
    r"Art\.?\s*\d+|Article\s+\d+|clause\s+\d+)\b",
    re.IGNORECASE,
)
# Exclusion signals: tokens that NEGATE ambiguity
_EXCLUSION_PAT = re.compile(
    r"(?:not|unlike|except|excluding|rather than|instead of|as opposed to)\s+(\S+(?:\s+\S+){0,3})",
    re.IGNORECASE,
)


@dataclass
class DisambigSignals:
    """Hard constraint tokens extracted from a query for disambiguation."""

    currency_hints: list[str] = field(default_factory=list)
    number_constraints: list[str] = field(default_factory=list)
    entity_names: list[str] = field(default_factory=list)
    standard_refs: list[str] = field(default_factory=list)
    exclusion_tokens: list[str] = field(default_factory=list)

    def all_tokens(self) -> list[str]:
        """All signal tokens for matching against chunk text."""
        return [
            *self.currency_hints,
            *self.number_constraints,
            *self.entity_names,
            *self.standard_refs,
        ]

    def has_signals(self) -> bool:
        return bool(
            self.currency_hints
            or self.number_constraints
            or self.entity_names
            or self.standard_refs
        )


def extract_disambiguation_signals(query: str) -> DisambigSignals:
    """Extract hard constraint tokens from a query."""
    signals = DisambigSignals()

    for m in _CURRENCY_PAT.finditer(query):
        token = m.group(1).strip()
        if token:
            signals.currency_hints.append(token)

    for m in _NUMBER_SPEC_PAT.finditer(query):
        token = m.group(0).strip()
        if token and token not in signals.currency_hints:
            signals.number_constraints.append(token)

    for m in _ENTITY_PAT.finditer(query):
        token = m.group(0).strip()
        if token:
            signals.entity_names.append(token)

    for m in _STANDARD_PAT.finditer(query):
        token = m.group(1).strip() if m.lastindex and m.group(1) else m.group(0).strip()
        if token:
            signals.standard_refs.append(token)

    for m in _EXCLUSION_PAT.finditer(query):
        token = m.group(1).strip()
        if token:
            signals.exclusion_tokens.append(token)

    return signals


def count_signal_hits(text: str, signals: DisambigSignals) -> int:
    """Count how many distinct signal tokens appear in the given text."""
    text_lower = text.lower()
    hits = 0
    for token in signals.all_tokens():
        if token.lower() in text_lower:
            hits += 1
    return hits


_SIGNAL_BONUS_PER_HIT = 0.08  # +8% score per signal hit (conservative)
_SIGNAL_EXCLUSION_PENALTY = 0.50  # -50% score for exclusion matches


def apply_signal_bonus(
    results: list,
    signals: DisambigSignals,
    *,
    bonus_per_hit: float = _SIGNAL_BONUS_PER_HIT,
    exclusion_penalty: float = _SIGNAL_EXCLUSION_PENALTY,
) -> list:
    """Re-weight results by signal token hits.

    Each distinct signal match adds bonus_per_hit * score.
    Exclusion token matches apply a penalty.
    Mutates and returns the same list, sorted by adjusted score.
    """
    if not signals.has_signals():
        return results

    for r in results:
        hits = count_signal_hits(r.text, signals)
        if hits > 0:
            r.score *= (1.0 + bonus_per_hit * hits)

        # Penalize chunks matching exclusion tokens
        if signals.exclusion_tokens:
            for excl in signals.exclusion_tokens:
                if excl.lower() in r.text.lower():
                    r.score *= (1.0 - exclusion_penalty)
                    break

    results.sort(key=lambda x: x.score, reverse=True)
    return results
