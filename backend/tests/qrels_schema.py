"""Stable qrels (query relevance judgments) data model for RAG evaluation.

Uses document_key + section_key as stable identifiers instead of
text Jaccard similarity, ensuring metrics stay within [0, 1] regardless
of chunk_size or overlap changes.

Grade scale: 3=perfect, 2=highly relevant, 1=partially relevant, 0=not relevant
"""

from __future__ import annotations

from dataclasses import dataclass, field


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
            section_key=d.get("section_key", ""),
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

    def to_dict(self) -> dict:
        return {
            "query_id": self.query_id,
            "query": self.query,
            "relevant": [r.to_dict() for r in self.relevant],
            "expected_answer_facts": self.expected_answer_facts,
            "must_cite": self.must_cite,
        }

    @classmethod
    def from_dict(cls, d: dict) -> QrelQuery:
        return cls(
            query_id=d["query_id"],
            query=d["query"],
            relevant=[RelevantItem.from_dict(r) for r in d.get("relevant", [])],
            expected_answer_facts=d.get("expected_answer_facts", []),
            must_cite=d.get("must_cite", []),
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
        key = re.sub(r"[^a-zA-Z0-9一-鿿_-]", "-", m.group(1).strip())
        return key.strip("-")[:max_len].lower()
    first_line = text.split("\n")[0].strip()[:max_len]
    return re.sub(r"[^a-zA-Z0-9一-鿿_-]", "-", first_line).strip("-").lower() or "section"
