"""Query type classifier for adaptive RRF weighting.

Identifies query patterns and returns weight profiles: semantic_weight
high for natural language, keyword_weight high for exact codes/SKUs.
"""

import re
from dataclasses import dataclass
from enum import Enum


class QueryType(Enum):
    EXACT_CODE = "exact_code"
    NUMERIC_SPEC = "numeric_spec"
    CLAUSE_LOOKUP = "clause_lookup"
    DRUG_LOOKUP = "drug_lookup"
    AMBIGUOUS_SHORT = "ambiguous_short"
    AMBIGUOUS_CROSS_DOMAIN = "ambiguous_cross_domain"
    SHORT_KEYWORD = "short_keyword"
    MIXED = "mixed"
    NATURAL_LANGUAGE = "natural_language"


@dataclass
class QueryProfile:
    semantic_weight: float
    keyword_weight: float


# Recognizable drug suffixes for heuristic matching
_DRUG_SUFFIXES = (
    "statin|sartan|prazole|dipine|olol|pril|mab|nib|tinib|"
    "ciclovir|cycline|floxacin|azepam|caine|tidine|trel|"
    "grel|taxel|platin|zomab|ximab|umab|tinib|rafenib|"
    "parib|gliptin|gliflozin|glutide|sertib|palbociclib"
)

# Common drug names (case-insensitive prefix match)
_COMMON_DRUGS = (
    r"\b(Clopidogrel|Atorvastatin|Nifedipine|Aspirin|Metformin|"
    r"Omeprazole|Ibuprofen|Paracetamol|Amoxicillin|Warfarin|"
    r"Simvastatin|Losartan|Amlodipine|Lisinopril|Metoprolol)\b"
)

# Units commonly found in numeric spec queries
_UNITS_PATTERN = r"\d+\s*(mg|g|mcg|°C|MSPS|MHz|GHz|kHz|bit|ppm|V|A|W|kV|mA|mW|mm|cm|km|ml|L|KB|MB|GB|TB|bps)"


def classify_query(query: str) -> QueryType:
    """Classify query for adaptive RRF weighting."""
    q = query.strip()

    # 1. Exact code: uppercase prefix + underscore/dash + digits
    #    e.g. ERR_40003, STM32H743VI, R7FA6M5BH3CFC
    if re.search(r"\b[A-Z]{2,6}[_-]\d{4,}[A-Z]?\b", q):
        return QueryType.EXACT_CODE

    # Long mixed alphanumeric: product SKU / part number (e.g. STM32H743VI)
    if re.search(r"\b[A-Z0-9]{8,}[A-Z]\b", q):
        return QueryType.EXACT_CODE
    # Product SKU with dashes: ESP32-S3R8, R7FA6M5BH3CFC
    if re.search(r"\b[A-Z0-9]+-[A-Z0-9]+[A-Z]\b", q):
        return QueryType.EXACT_CODE

    # 2. Drug lookup: known drug names (check BEFORE numeric to catch "Simvastatin 20mg")
    if re.search(_COMMON_DRUGS, q, re.IGNORECASE):
        return QueryType.DRUG_LOOKUP

    # Drug name pattern: Capitalized word with pharmaceutical suffix
    if re.search(rf"\b[A-Z][a-z]+(?:{_DRUG_SUFFIXES})\b", q, re.IGNORECASE):
        return QueryType.DRUG_LOOKUP

    # 3. Numeric spec: contains units
    if re.search(_UNITS_PATTERN, q, re.IGNORECASE):
        return QueryType.NUMERIC_SPEC

    # Numeric range pattern: "X to Y" with units
    if re.search(r"-?\d+\s*(C|F|K)\s+to\s+-?\d+\s*(C|F|K)", q, re.IGNORECASE):
        return QueryType.NUMERIC_SPEC

    # 5. Clause lookup: "clause" + number
    if re.search(r"clause\s+\d+", q, re.IGNORECASE):
        return QueryType.CLAUSE_LOOKUP

    # 6. Ambiguous short: <= 3 words, common concept terms, no specific entity
    words = q.split()
    if len(words) <= 3 and _is_ambiguous_short(q, words):
        return QueryType.AMBIGUOUS_SHORT

    # 7. Short keyword: <= 2 words, no question structure, not ambiguous
    if len(words) <= 2 and not _has_question_structure(q):
        return QueryType.SHORT_KEYWORD

    # 9. Ambiguous cross-domain: natural language query lacking entity anchors
    if _has_question_structure(q) and _is_cross_domain_ambiguous(q, words):
        return QueryType.AMBIGUOUS_CROSS_DOMAIN

    # 10. Natural language: contains question words or sentence markers
    if _has_question_structure(q):
        return QueryType.NATURAL_LANGUAGE

    # 11. Mixed: contains code-like tokens AND natural language
    if re.search(r"[A-Z]{2,}", q) and len(words) >= 3:
        return QueryType.MIXED

    return QueryType.NATURAL_LANGUAGE


# ── Ambiguity detection ─────────────────────────────────────────

# Words that commonly appear across multiple documents/domains,
# making queries about them inherently ambiguous without entity anchors.
_AMBIGUOUS_CONCEPT_WORDS = {
    "refund", "breach", "notification", "deletion", "encryption",
    "accuracy", "specification", "requirement", "compliance",
    "authentication", "authorization", "validation", "verification",
    "cancellation", "termination", "suspension", "revocation",
    "registration", "configuration", "integration", "migration",
    "transfer", "retention", "disposal", "monitoring", "audit",
    "reporting", "approval", "threshold", "limitation", "exception",
    "penalty", "compensation", "liability", "warranty", "coverage",
    "support", "maintenance", "upgrade", "backup", "recovery",
}

# Terms that act as entity anchors — their presence reduces ambiguity.
_ENTITY_ANCHOR_PATTERNS = [
    re.compile(r"\b[A-Z]{2,6}[_-]\d{4,}", re.IGNORECASE),  # ERR_40003
    re.compile(r"\b[A-Z][a-z]+(?:statin|sartan|prazole|dipine|olol|pril|mab|nib)\b", re.IGNORECASE),
    re.compile(r"\b(GDPR|PIPL|PCI|DSS|HIPAA|SOX|ISO|NIST|FIPS|FedRAMP)\b"),
    re.compile(r"\b[A-Z]{3,}\b"),   # all-caps acronyms (SHT45, BME280, HDC3020)
    re.compile(r"\b[A-Z][a-z]+ [A-Z][a-z]+\b"),  # Proper Name pairs
    re.compile(r"\d+\s*(?:EUR|CNY|USD|GBP|JPY)\b", re.IGNORECASE),
    re.compile(r"\b(?:Art\.?\s*\d+|clause\s+\d+|section\s+\d+)\b", re.IGNORECASE),
]


def _has_entity_anchor(q: str) -> bool:
    """Check if query contains terms that pin it to a specific entity/document."""
    return any(pat.search(q) for pat in _ENTITY_ANCHOR_PATTERNS)


def _is_ambiguous_short(q: str, words: list[str]) -> bool:
    """Short query (≤3 words) composed of common concept terms, no entity anchors.

    Examples: "refund", "encryption", "accuracy"
    Non-examples: "ERR_40003" (has entity anchor), "STM32H743VI" (has code pattern)
    """
    # Must not have entity anchors (codes, standards, brand names)
    if _has_entity_anchor(q):
        return False
    # At least one word must be a known ambiguous concept
    return any(w.lower() in _AMBIGUOUS_CONCEPT_WORDS for w in words)


def _is_cross_domain_ambiguous(q: str, words: list[str]) -> bool:
    """Natural-language query about a general concept lacking entity anchors.

    Examples: "how to handle a refund when payment is not confirmed",
              "what is the deletion right when consent withdrawn"
    Non-examples: "what is GDPR Art.17 deletion right" (has entity anchor)
    """
    if _has_entity_anchor(q):
        return False
    # Must contain at least one ambiguous concept word
    return any(w.lower() in _AMBIGUOUS_CONCEPT_WORDS for w in words)


def _has_question_structure(q: str) -> bool:
    """Check if query looks like a natural language question/request."""
    ql = q.lower()
    question_words = (
        "what", "how", "which", "where", "when", "why", "who",
        "parameters", "required", "support", "explain",
        "described", "mentioned", "recommend",
    )
    return any(w in ql for w in question_words)


# Default weight profiles — semantic_weight higher favors semantic search
# keyword_weight higher favors keyword/FTS5 search
DEFAULT_PROFILES: dict[QueryType, QueryProfile] = {
    QueryType.EXACT_CODE: QueryProfile(semantic_weight=0.3, keyword_weight=3.0),
    QueryType.NUMERIC_SPEC: QueryProfile(semantic_weight=0.5, keyword_weight=2.5),
    QueryType.CLAUSE_LOOKUP: QueryProfile(semantic_weight=1.0, keyword_weight=2.5),
    QueryType.DRUG_LOOKUP: QueryProfile(semantic_weight=1.5, keyword_weight=2.0),
    QueryType.AMBIGUOUS_SHORT: QueryProfile(semantic_weight=0.5, keyword_weight=3.0),
    QueryType.AMBIGUOUS_CROSS_DOMAIN: QueryProfile(semantic_weight=0.8, keyword_weight=2.5),
    QueryType.SHORT_KEYWORD: QueryProfile(semantic_weight=1.0, keyword_weight=1.5),
    QueryType.MIXED: QueryProfile(semantic_weight=1.5, keyword_weight=1.5),
    QueryType.NATURAL_LANGUAGE: QueryProfile(semantic_weight=3.0, keyword_weight=0.5),
}


def get_profile(query: str) -> QueryProfile:
    """Get the weight profile for a query."""
    qtype = classify_query(query)
    return DEFAULT_PROFILES[qtype]
