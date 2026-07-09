"""Query type classifier for adaptive RRF weighting.

Identifies query patterns and returns weight profiles: semantic_weight
high for natural language, keyword_weight high for exact codes/SKUs.
"""

import re
from dataclasses import dataclass
from enum import Enum
from typing import ClassVar


class QueryType(Enum):
    EXACT_CODE = "exact_code"
    NUMERIC_SPEC = "numeric_spec"
    CLAUSE_LOOKUP = "clause_lookup"
    DRUG_LOOKUP = "drug_lookup"
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

    # 6. Short keyword: <= 2 words, no question structure
    words = q.split()
    if len(words) <= 2 and not _has_question_structure(q):
        return QueryType.SHORT_KEYWORD

    # 7. Natural language: contains question words or sentence markers
    if _has_question_structure(q):
        return QueryType.NATURAL_LANGUAGE

    # 8. Mixed: contains code-like tokens AND natural language
    if re.search(r"[A-Z]{2,}", q) and len(words) >= 3:
        return QueryType.MIXED

    return QueryType.NATURAL_LANGUAGE


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
DEFAULT_PROFILES: ClassVar[dict[QueryType, QueryProfile]] = {
    QueryType.EXACT_CODE: QueryProfile(semantic_weight=0.3, keyword_weight=3.0),
    QueryType.NUMERIC_SPEC: QueryProfile(semantic_weight=0.5, keyword_weight=2.5),
    QueryType.CLAUSE_LOOKUP: QueryProfile(semantic_weight=1.0, keyword_weight=2.5),
    QueryType.DRUG_LOOKUP: QueryProfile(semantic_weight=1.5, keyword_weight=2.0),
    QueryType.SHORT_KEYWORD: QueryProfile(semantic_weight=1.0, keyword_weight=1.5),
    QueryType.MIXED: QueryProfile(semantic_weight=1.5, keyword_weight=1.5),
    QueryType.NATURAL_LANGUAGE: QueryProfile(semantic_weight=3.0, keyword_weight=0.5),
}


def get_profile(query: str) -> QueryProfile:
    """Get the weight profile for a query."""
    qtype = classify_query(query)
    return DEFAULT_PROFILES[qtype]
