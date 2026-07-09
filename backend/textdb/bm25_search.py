"""BM25 keyword search with jieba tokenization and SQLite storage.

Replaces SQLiteFTS5 with a proper BM25 implementation that:
- Uses jieba for Chinese word segmentation
- Preserves code-like tokens intact (ERR_40003, STM32H743VI)
- Computes IDF from collection statistics
- Uses standard BM25 scoring (k1=1.5, b=0.75)
"""

import logging
import math
import re
from collections import Counter
from typing import Any

import jieba

from models.database import async_session

from .base import BaseTextDB, TextSearchResult

logger = logging.getLogger(__name__)

BM25_K1 = 1.5
BM25_B = 0.75

# Characters to strip from token boundaries
_TOKEN_BOUNDARY = re.compile(r"^[\s\W]+|[\s\W]+$")

# Tokens that could be part of a code: has digit, or short-pure-alpha near code
_CODE_FRAGMENT = re.compile(r"^[A-Za-z0-9]*[0-9][A-Za-z0-9]*$")  # contains digit
_SHORT_ALPHA = re.compile(r"^[A-Za-z]{1,5}$")  # short alpha, could be code prefix
_SEPARATOR = re.compile(r"^[_\-\.]$")  # connector chars


def tokenize(text: str) -> list[str]:
    """Tokenize mixed Chinese-English text. Preserves code-like tokens."""
    raw_tokens = [w.strip() for w in jieba.cut(text) if w.strip()]

    # Phase 1: clean individual tokens
    cleaned_tokens: list[str] = []
    for t in raw_tokens:
        cleaned = _TOKEN_BOUNDARY.sub("", t)
        if not cleaned:
            continue
        if re.fullmatch(r"[\s\W]+", cleaned):
            continue
        if cleaned.isspace():
            continue
        cleaned_tokens.append(cleaned.lower())

    # Phase 2: re-join code-like fragments split by jieba
    # Pattern: [short_alpha] + (sep|code_fragment)* → one code token
    # e.g. ['err', '_', '40003'] → 'err_40003'
    # e.g. ['stm32h743vi', 'mcu'] → stay separate ('mcu' is not sep/code)
    tokens: list[str] = []
    buf: list[str] = []
    for t in cleaned_tokens:
        is_code = _CODE_FRAGMENT.match(t) is not None
        is_sep = _SEPARATOR.match(t) is not None
        is_short = _SHORT_ALPHA.match(t) is not None

        if buf:
            # Continue buf: only separators or code fragments
            if is_sep or is_code:
                buf.append(t)
                continue
            else:
                # Flush buf and re-check current token
                joined = "".join(buf)
                if re.search(r"[0-9]", joined):
                    tokens.append(joined)
                else:
                    tokens.extend(buf)
                buf.clear()
                # fall through to re-check this token

        if not buf:
            # Start new buf: short alpha or code fragment
            if is_short or is_code:
                buf.append(t)
            else:
                tokens.append(t)

    if buf:
        joined = "".join(buf)
        if re.search(r"[0-9]", joined):
            tokens.append(joined)
        else:
            tokens.extend(buf)

    return tokens


class BM25Search(BaseTextDB):
    """BM25 keyword search backed by SQLite inverted index."""

    def __init__(self, table_suffix: str = ""):
        self._table_suffix = table_suffix
        self._docs = f"bm25_docs{self._table_suffix}"
        self._idx = f"bm25_index{self._table_suffix}"
        self._stats = f"bm25_stats{self._table_suffix}"
        self._cache: dict[str, Any] | None = None  # avgdl + N cache
        self._tables_created = False

    async def _ensure_tables(self) -> None:
        """Create tables if they don't exist (for test isolation or rebuild)."""
        if self._tables_created:
            return
        stmts = [
            f"CREATE TABLE IF NOT EXISTS {self._docs} ("
            "  chunk_id TEXT PRIMARY KEY, document_id TEXT NOT NULL,"
            "  text TEXT NOT NULL, token_count INTEGER NOT NULL DEFAULT 0)",
            f"CREATE TABLE IF NOT EXISTS {self._idx} ("
            "  term TEXT NOT NULL, chunk_id TEXT NOT NULL, tf INTEGER NOT NULL,"
            "  PRIMARY KEY (term, chunk_id))",
            f"CREATE TABLE IF NOT EXISTS {self._stats} ("
            "  term TEXT PRIMARY KEY, df INTEGER NOT NULL DEFAULT 0)",
            f"CREATE INDEX IF NOT EXISTS idx_{self._docs}_did ON {self._docs}(document_id)",
            f"CREATE INDEX IF NOT EXISTS idx_{self._idx}_term ON {self._idx}(term)",
        ]
        await self._batch_exec(stmts)
        self._tables_created = True

    async def _batch_exec(self, statements: list[str]) -> None:
        """Execute multiple SQL statements in a single transaction-like batch."""
        async with async_session() as session:
            conn = await session.connection()
            for sql in statements:
                await conn.exec_driver_sql(sql)
            await session.commit()

    async def _query(self, sql: str) -> list[Any]:
        """Execute a SELECT query and return all rows."""
        async with async_session() as session:
            conn = await session.connection()
            result = await conn.exec_driver_sql(sql)
            return list(result.fetchall())

    async def _load_stats(self) -> dict[str, Any]:
        """Load collection-level statistics (cached per session)."""
        if self._cache is not None:
            return self._cache

        rows = await self._query(
            f"SELECT COUNT(*) FROM {self._docs}"
        )
        n = rows[0][0] if rows else 0

        if n == 0:
            avgdl = 0.0
        else:
            avgdl_rows = await self._query(
                f"SELECT AVG(token_count) FROM {self._docs}"
            )
            avgdl = float(avgdl_rows[0][0] or 0)

        self._cache = {"N": n, "avgdl": avgdl}
        return self._cache

    def _invalidate_cache(self) -> None:
        self._cache = None

    # ── BaseTextDB interface ──────────────────────────────────────

    async def insert(self, chunk_id: str, document_id: str, text: str) -> None:
        await self._ensure_tables()
        tokens = tokenize(text)
        tf_counter = Counter(tokens)
        token_count = len(tokens)

        # Escape single quotes for SQL
        safe_text = text.replace("'", "''")
        safe_cid = chunk_id.replace("'", "''")
        safe_did = document_id.replace("'", "''")

        stmts = [
            f"INSERT OR REPLACE INTO {self._docs} (chunk_id, document_id, text, token_count) "
            f"VALUES ('{safe_cid}', '{safe_did}', '{safe_text}', {token_count})",
        ]

        for term, tf in tf_counter.items():
            safe_term = term.replace("'", "''")
            stmts.append(
                f"INSERT INTO {self._idx} (term, chunk_id, tf) "
                f"VALUES ('{safe_term}', '{safe_cid}', {tf}) "
                f"ON CONFLICT(term, chunk_id) DO UPDATE SET tf = {tf}"
            )

        # Update df for each unique term
        for term in tf_counter:
            safe_term = term.replace("'", "''")
            stmts.append(
                f"INSERT INTO {self._stats} (term, df) "
                f"VALUES ('{safe_term}', 1) "
                f"ON CONFLICT(term) DO UPDATE SET df = df + 1"
            )

        await self._batch_exec(stmts)
        self._invalidate_cache()

    async def search(
        self, query: str, top_k: int = 10, document_id: str = ""
    ) -> list[TextSearchResult]:
        await self._ensure_tables()
        query_tokens = tokenize(query)
        if not query_tokens:
            return []

        stats = await self._load_stats()
        N = stats["N"]  # noqa: N806
        avgdl = stats["avgdl"]

        if N == 0 or avgdl == 0:
            return []

        # Collect candidate chunks from index for each query token
        candidates: dict[str, dict[str, Any]] = {}
        for t in query_tokens:
            safe_term = t.replace("'", "''")
            sql = (
                f"SELECT i.chunk_id, i.tf, d.token_count, d.text, d.document_id "
                f"FROM {self._idx} i "
                f"JOIN {self._docs} d ON i.chunk_id = d.chunk_id "
                f"WHERE i.term = '{safe_term}'"
            )
            if document_id:
                safe_did = document_id.replace("'", "''")
                sql += f" AND d.document_id = '{safe_did}'"

            rows = await self._query(sql)
            for row in rows:
                chunk_id = row[0]
                if chunk_id not in candidates:
                    candidates[chunk_id] = {
                        "chunk_id": chunk_id,
                        "document_id": row[4],
                        "text": row[3],
                        "token_count": row[2],
                        "term_tfs": {},
                    }
                candidates[chunk_id]["term_tfs"][t] = row[1]

        if not candidates:
            return []

        # Compute IDF for each query token (batch query)
        safe_terms = [t.replace("'", "''") for t in query_tokens]
        terms_in = ", ".join(f"'{t}'" for t in safe_terms)
        idf_rows = await self._query(
            f"SELECT term, df FROM {self._stats} WHERE term IN ({terms_in})"
        )
        df_map = {row[0]: row[1] for row in idf_rows}

        # Compute BM25 scores
        scored: list[tuple[float, str, str, str]] = []
        for chunk_id, c in candidates.items():
            score = 0.0
            dl = c["token_count"]
            for t, tf in c["term_tfs"].items():
                df = df_map.get(t, 1)
                idf = math.log((N - df + 0.5) / (df + 0.5) + 1.0)
                numerator = tf * (BM25_K1 + 1.0)
                denominator = tf + BM25_K1 * (1.0 - BM25_B + BM25_B * dl / avgdl)
                score += idf * numerator / denominator
            scored.append((score, chunk_id, c["document_id"], c["text"]))

        scored.sort(key=lambda x: x[0], reverse=True)

        return [
            TextSearchResult(
                chunk_id=chunk_id,
                document_id=doc_id,
                text=text,
                score=score,
            )
            for score, chunk_id, doc_id, text in scored[:top_k]
        ]

    async def delete_by_document(self, document_id: str) -> None:
        await self._ensure_tables()
        safe_did = document_id.replace("'", "''")

        # Collect chunk_ids for this document before deleting
        rows = await self._query(
            f"SELECT chunk_id FROM {self._docs} WHERE document_id = '{safe_did}'"
        )
        chunk_ids = [r[0] for r in rows]

        if not chunk_ids:
            return

        # Incrementally update stats before deleting
        await self._increment_terms(chunk_ids, delta=-1)

        # Delete from index and docs
        chunk_ids_in = ", ".join(f"'{cid.replace(chr(39), chr(39)+chr(39))}'" for cid in chunk_ids)
        await self._batch_exec([
            f"DELETE FROM {self._idx} WHERE chunk_id IN ({chunk_ids_in})",
            f"DELETE FROM {self._docs} WHERE document_id = '{safe_did}'",
        ])

        self._invalidate_cache()

    async def delete_by_chunks(self, chunk_ids: list[str]) -> None:
        await self._ensure_tables()
        if not chunk_ids:
            return
        await self._increment_terms(chunk_ids, delta=-1)
        safe_ids = ", ".join(
            f"'{cid.replace(chr(39), chr(39)+chr(39))}'" for cid in chunk_ids
        )
        await self._batch_exec([
            f"DELETE FROM {self._idx} WHERE chunk_id IN ({safe_ids})",
            f"DELETE FROM {self._docs} WHERE chunk_id IN ({safe_ids})",
        ])
        self._invalidate_cache()

    async def count(self) -> int:
        await self._ensure_tables()
        rows = await self._query(f"SELECT COUNT(*) FROM {self._docs}")
        return rows[0][0] if rows else 0

    # ── Helper methods ───────────────────────────────────────────

    async def _increment_terms(self, chunk_ids: list[str], delta: int = -1) -> None:
        """Increment/decrement df for unique terms in given chunks."""
        if not chunk_ids:
            return
        safe_ids = ", ".join(
            f"'{cid.replace(chr(39), chr(39)+chr(39))}'" for cid in chunk_ids
        )
        rows = await self._query(
            f"SELECT DISTINCT term FROM {self._idx} WHERE chunk_id IN ({safe_ids})"
        )
        if not rows:
            return
        stmts: list[str] = []
        for (term,) in rows:
            safe = term.replace("'", "''")
            if delta < 0:
                stmts.append(
                    f"UPDATE {self._stats} SET df = MAX(0, df + {delta}) "
                    f"WHERE term = '{safe}'"
                )
                stmts.append(
                    f"DELETE FROM {self._stats} WHERE term = '{safe}' AND df <= 0"
                )
            else:
                stmts.append(
                    f"INSERT INTO {self._stats} (term, df) VALUES ('{safe}', 1) "
                    f"ON CONFLICT(term) DO UPDATE SET df = df + {delta}"
                )
        await self._batch_exec(stmts)

    async def insert_batch(
        self, entries: list[tuple[str, str, str]]
    ) -> None:
        """Insert multiple chunks in a single transaction.
        entries: [(chunk_id, document_id, text), ...]
        """
        if not entries:
            return
        await self._ensure_tables()

        stmts: list[str] = []
        all_terms: dict[str, set[str]] = {}  # term -> set of chunk_ids

        for chunk_id, document_id, text in entries:
            tokens = tokenize(text)
            tf_counter: dict[str, int] = {}
            for t in tokens:
                tf_counter[t] = tf_counter.get(t, 0) + 1

            safe_text = text.replace("'", "''")
            safe_cid = chunk_id.replace("'", "''")
            safe_did = document_id.replace("'", "''")
            token_count = len(tokens)

            stmts.append(
                f"INSERT OR REPLACE INTO {self._docs} (chunk_id, document_id, text, token_count) "
                f"VALUES ('{safe_cid}', '{safe_did}', '{safe_text}', {token_count})"
            )
            for term, tf in tf_counter.items():
                safe_term = term.replace("'", "''")
                stmts.append(
                    f"INSERT INTO {self._idx} (term, chunk_id, tf) "
                    f"VALUES ('{safe_term}', '{safe_cid}', {tf}) "
                    f"ON CONFLICT(term, chunk_id) DO UPDATE SET tf = {tf}"
                )
                if term not in all_terms:
                    all_terms[term] = set()
                all_terms[term].add(chunk_id)

        # Stats: increment df for each unique (term, chunk) pair
        for term in all_terms:
            safe_term = term.replace("'", "''")
            stmts.append(
                f"INSERT INTO {self._stats} (term, df) VALUES ('{safe_term}', 1) "
                f"ON CONFLICT(term) DO UPDATE SET df = df + 1"
            )

        await self._batch_exec(stmts)
        self._invalidate_cache()

    async def raw_query(self, sql: str) -> list[Any]:
        """Exposed for settings.py rebuild logic."""
        return await self._query(sql)
