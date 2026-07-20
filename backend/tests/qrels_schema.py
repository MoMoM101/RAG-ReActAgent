"""Stable qrels (query relevance judgments) data model for RAG evaluation.

Uses document_key + section_key as stable identifiers instead of
text Jaccard similarity, ensuring metrics stay within [0, 1] regardless
of chunk_size or overlap changes.

Grade scale: 3=perfect, 2=highly relevant, 1=partially relevant, 0=not relevant
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

Answerability = Literal["full", "partial", "none"]


def normalize_section_key(value: str, max_len: int = 30) -> str:
    """Canonicalize qrels keys with the same rules as the production splitter."""
    import re
    return re.sub(r"[^a-zA-Z0-9一-鿿_-]", "-", value.strip()).strip("-")[:max_len].lower()


@dataclass
class RelevantItem:
    """A single relevant passage identified by stable keys."""
    document_key: str          # stable doc id, e.g. "paygate"
    section_key: str           # stable section id, e.g. "error-40003"
    grade: int = 3             # 1-3 relevance grade

    def to_dict(self) -> dict:
        return {
            "document_key": self.document_key,
            "section_key": self.section_key,
            "grade": self.grade,
        }

    @classmethod
    def from_dict(cls, d: dict) -> RelevantItem:
        return cls(
            document_key=d["document_key"],
            section_key=normalize_section_key(d.get("section_key", "")),
            grade=d.get("grade", 3),
        )


@dataclass
class QrelQuery:
    """A single evaluation query with relevance judgments."""
    query_id: str              # unique id, e.g. "exact-001"
    query: str                 # the natural language query
    relevant: list[RelevantItem] = field(default_factory=list)
    expected_answer_facts: list[str] = field(default_factory=list)  # for answer eval
    must_cite: list[str] = field(default_factory=list)  # e.g. ["paygate#error-40003"]
    # Retrieval relevance and answerability are deliberately separate. A passage
    # may mention the queried entities without containing the requested relation.
    answerability: Answerability = "full"
    # Each entry is one semantic fact. Human annotators may separate accepted
    # surface forms with ``|`` (for example ``feta|菲达``); matching any one
    # expression satisfies that fact without requiring every synonym.
    answer_expected_facts: list[str] = field(default_factory=list)
    answerability_rationale: str = ""

    def to_dict(self) -> dict:
        return {
            "query_id": self.query_id,
            "query": self.query,
            "relevant": [r.to_dict() for r in self.relevant],
            "expected_answer_facts": self.expected_answer_facts,
            "must_cite": self.must_cite,
            "answerability": self.answerability,
            "answer_expected_facts": self.answer_expected_facts,
            "answerability_rationale": self.answerability_rationale,
        }

    @classmethod
    def from_dict(cls, d: dict) -> QrelQuery:
        inferred = "full" if d.get("relevant", []) else "none"
        answerability = d.get("answerability", inferred)
        if answerability not in {"full", "partial", "none"}:
            raise ValueError(f"invalid answerability: {answerability}")
        return cls(
            query_id=d["query_id"],
            query=d["query"],
            relevant=[RelevantItem.from_dict(r) for r in d.get("relevant", [])],
            expected_answer_facts=d.get("expected_answer_facts", []),
            must_cite=d.get("must_cite", []),
            answerability=answerability,
            answer_expected_facts=d.get("answer_expected_facts", []),
            answerability_rationale=d.get("answerability_rationale", ""),
        )


@dataclass
class QrelDataset:
    """Complete evaluation dataset with train/dev/test splits."""
    name: str
    version: str = "1.0"
    queries: list[QrelQuery] = field(default_factory=list)
    document_keys: dict[str, str] = field(default_factory=dict)  # filename → document_key

    @classmethod
    def load(cls, path: str) -> QrelDataset:
        import json
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        return cls(
            name=data.get("name", ""),
            version=data.get("version", "1.0"),
            queries=[QrelQuery.from_dict(q) for q in data.get("queries", [])],
            document_keys=data.get("document_keys", {}),
        )

    def save(self, path: str) -> None:
        import json
        with open(path, "w", encoding="utf-8") as f:
            json.dump(self.to_dict(), f, indent=2, ensure_ascii=False)

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "version": self.version,
            "document_keys": self.document_keys,
            "queries": [q.to_dict() for q in self.queries],
        }


def document_key_from_filename(filename: str) -> str:
    """Derive a stable document_key from a filename.

    Strips extension and replaces special chars with '-'.
    """
    import re
    base = filename.rsplit(".", 1)[0] if "." in filename else filename
    return re.sub(r"[^a-zA-Z0-9-]", "-", base).strip("-").lower() or "doc"


def section_key_from_text(text: str, max_len: int = 30) -> str:
    """Extract a stable section_key from chunk text (first markdown header or first line)."""
    import re
    m = re.search(r"^#{1,6}\s+(.+)$", text, re.MULTILINE)
    if m:
        return normalize_section_key(m.group(1), max_len)
    first_line = text.split("\n")[0].strip()[:max_len]
    return normalize_section_key(first_line, max_len) or "section"
