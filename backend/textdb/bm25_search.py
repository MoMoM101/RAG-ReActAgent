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
from sqlalchemy import text as sa_text

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
    tokens: list[str] = []
    buf: list[str] = []
    for t in cleaned_tokens:
        is_code = _CODE_FRAGMENT.match(t) is not None
        is_sep = _SEPARATOR.match(t) is not None
        is_short = _SHORT_ALPHA.match(t) is not None

        if buf:
            if is_sep or is_code:
                buf.append(t)
                continue
            else:
                joined = "".join(buf)
                if re.search(r"[0-9]", joined):
                    tokens.append(joined)
                else:
                    tokens.extend(buf)
                buf.clear()

        if not buf:
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
            "  document_key TEXT NOT NULL DEFAULT '',"
            "  section_key TEXT NOT NULL DEFAULT '',"
            "  chunk_index INTEGER NOT NULL DEFAULT 0,"
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
        """Execute raw DDL statements (no user data, safe for exec_driver_sql)."""
        async with async_session() as session:
            conn = await session.connection()
            for sql in statements:
                await conn.exec_driver_sql(sql)
            await session.commit()

    async def _batch_param(self, stmts: list[tuple[str, dict]]) -> None:
        """Execute parameterized DML statements in a single transaction."""
        if not stmts:
            return
        async with async_session() as session:
            conn = await session.connection()
            for sql, params in stmts:
                await conn.execute(sa_text(sql), params)
            await session.commit()

    async def _query(self, sql: str) -> list[Any]:
        """Execute a SELECT query (no user params, for internal use)."""
        async with async_session() as session:
            conn = await session.connection()
            result = await conn.exec_driver_sql(sql)
            return list(result.fetchall())

    async def _query_param(self, sql: str, params: dict) -> list[Any]:
        """Execute a parameterized SELECT query."""
        async with async_session() as session:
            conn = await session.connection()
            result = await conn.execute(sa_text(sql), params)
            return list(result.fetchall())

    async def _exec_param(self, sql: str, params: dict) -> None:
        """Execute a parameterized DML statement (INSERT/UPDATE/DELETE)."""
        async with async_session() as session:
            conn = await session.connection()
            await conn.execute(sa_text(sql), params)
            await session.commit()

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

    async def insert(self, chunk_id: str, document_id: str, text: str,
                     document_key: str = "", section_key: str = "",
                     chunk_index: int = 0) -> None:
        await self._ensure_tables()
        tokens = tokenize(text)
        tf_counter = Counter(tokens)
        token_count = len(tokens)

        stmts: list[tuple[str, dict]] = []

        # Step 1: remove old posting and decrement df for re-inserts
        old_rows = await self._query_param(
            f"SELECT term FROM {self._idx} WHERE chunk_id = :cid",
            {"cid": chunk_id},
        )
        if old_rows:
            old_terms = {row[0] for row in old_rows}
            stmts.append((
                f"DELETE FROM {self._idx} WHERE chunk_id = :cid",
                {"cid": chunk_id},
            ))
            for term in old_terms:
                stmts.append((
                    f"UPDATE {self._stats} SET df = MAX(0, df - 1) WHERE term = :term",
                    {"term": term},
                ))
                stmts.append((
                    f"DELETE FROM {self._stats} WHERE term = :term AND df <= 0",
                    {"term": term},
                ))

        # Step 2: write new doc record
        stmts.append((
            f"INSERT OR REPLACE INTO {self._docs} "
            f"(chunk_id, document_id, document_key, section_key, chunk_index, text, token_count) "
            f"VALUES (:cid, :did, :dkey, :skey, :cindex, :text, :tokens)",
            {"cid": chunk_id, "did": document_id, "dkey": document_key,
             "skey": section_key, "cindex": chunk_index,
             "text": text, "tokens": token_count},
        ))

        # Step 3: write new index entries
        for term, tf in tf_counter.items():
            stmts.append((
                f"INSERT INTO {self._idx} (term, chunk_id, tf) "
                f"VALUES (:term, :cid, :tf) "
                f"ON CONFLICT(term, chunk_id) DO UPDATE SET tf = :tf2",
                {"term": term, "cid": chunk_id, "tf": tf, "tf2": tf},
            ))

        # Step 4: increment df for new terms
        for term in tf_counter:
            stmts.append((
                f"INSERT INTO {self._stats} (term, df) VALUES (:term, 1) "
                f"ON CONFLICT(term) DO UPDATE SET df = df + 1",
                {"term": term},
            ))

        await self._batch_param(stmts)
        self._invalidate_cache()

    async def insert_batch(
        self, entries: list[tuple[str, str, str, str, int, str]]
    ) -> None:
        """Insert multiple chunks in a single transaction.
        entries: [(chunk_id, document_id, document_key, section_key, chunk_index, text), ...]
        """
        if not entries:
            return
        await self._ensure_tables()

        stmts: list[tuple[str, dict]] = []

        # Step 1: remove old postings and decrement df for re-inserted chunks
        entry_chunk_ids = [e[0] for e in entries]
        placeholders = ", ".join(f":c{i}" for i in range(len(entry_chunk_ids)))
        cid_params = {f"c{i}": cid for i, cid in enumerate(entry_chunk_ids)}
        old_rows = await self._query_param(
            f"SELECT term, chunk_id FROM {self._idx} "
            f"WHERE chunk_id IN ({placeholders})",
            cid_params,
        )
        if old_rows:
            # Collect old term -> set of chunk_ids for df adjustment
            old_term_chunks: dict[str, set[str]] = {}
            for term, cid in old_rows:
                if term not in old_term_chunks:
                    old_term_chunks[term] = set()
                old_term_chunks[term].add(cid)

            stmts.append((
                f"DELETE FROM {self._idx} WHERE chunk_id IN ({placeholders})",
                dict(cid_params),
            ))
            for term, cid_set in old_term_chunks.items():
                delta = len(cid_set)
                stmts.append((
                    f"UPDATE {self._stats} SET df = MAX(0, df - :delta) WHERE term = :term",
                    {"term": term, "delta": delta},
                ))
                stmts.append((
                    f"DELETE FROM {self._stats} WHERE term = :term AND df <= 0",
                    {"term": term},
                ))

        # Step 2: write new docs and index entries
        all_terms: dict[str, set[str]] = {}  # term -> set of chunk_ids

        for chunk_id, document_id, document_key, section_key, chunk_index, text in entries:
            tokens = tokenize(text)
            tf_counter: dict[str, int] = {}
            for t in tokens:
                tf_counter[t] = tf_counter.get(t, 0) + 1

            token_count = len(tokens)

            stmts.append((
                f"INSERT OR REPLACE INTO {self._docs} "
                f"(chunk_id, document_id, document_key, section_key, chunk_index, text, token_count) "
                f"VALUES (:cid, :did, :dkey, :skey, :cindex, :text, :tokens)",
                {"cid": chunk_id, "did": document_id, "dkey": document_key,
                 "skey": section_key, "cindex": chunk_index,
                 "text": text, "tokens": token_count},
            ))
            for term, tf in tf_counter.items():
                stmts.append((
                    f"INSERT INTO {self._idx} (term, chunk_id, tf) "
                    f"VALUES (:term, :cid, :tf) "
                    f"ON CONFLICT(term, chunk_id) DO UPDATE SET tf = :tf2",
                    {"term": term, "cid": chunk_id, "tf": tf, "tf2": tf},
                ))
                if term not in all_terms:
                    all_terms[term] = set()
                all_terms[term].add(chunk_id)

        # Step 3: increment df by number of chunks containing each term
        for term, chunk_ids in all_terms.items():
            delta = len(chunk_ids)
            stmts.append((
                f"INSERT INTO {self._stats} (term, df) VALUES (:term, :delta) "
                f"ON CONFLICT(term) DO UPDATE SET df = df + :delta2",
                {"term": term, "delta": delta, "delta2": delta},
            ))

        await self._batch_param(stmts)
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
            sql = (
                f"SELECT i.chunk_id, i.tf, d.token_count, d.text, d.document_id,"
                f" d.document_key, d.section_key "
                f"FROM {self._idx} i "
                f"JOIN {self._docs} d ON i.chunk_id = d.chunk_id "
                f"WHERE i.term = :term"
            )
            params: dict = {"term": t}
            if document_id:
                sql += " AND d.document_id = :did"
                params["did"] = document_id

            rows = await self._query_param(sql, params)
            for row in rows:
                chunk_id = row[0]
                if chunk_id not in candidates:
                    candidates[chunk_id] = {
                        "chunk_id": chunk_id,
                        "document_id": row[4],
                        "document_key": row[5],
                        "section_key": row[6],
                        "text": row[3],
                        "token_count": row[2],
                        "term_tfs": {},
                    }
                candidates[chunk_id]["term_tfs"][t] = row[1]

        if not candidates:
            return []

        # Compute IDF for each query token (batch query with parameterized IN)
        if query_tokens:
            placeholders = ", ".join(f":t{i}" for i in range(len(query_tokens)))
            idf_params = {f"t{i}": t for i, t in enumerate(query_tokens)}
            idf_rows = await self._query_param(
                f"SELECT term, df FROM {self._stats} WHERE term IN ({placeholders})",
                idf_params,
            )
            df_map: dict[str, int] = {row[0]: row[1] for row in idf_rows}
        else:
            df_map = {}

        # Compute BM25 scores
        scored: list[tuple[float, str, str, str, str, str]] = []
        for chunk_id, c in candidates.items():
            score = 0.0
            dl = c["token_count"]
            for t, tf in c["term_tfs"].items():
                df = df_map.get(t, 1)
                idf = math.log((N - df + 0.5) / (df + 0.5) + 1.0)
                numerator = tf * (BM25_K1 + 1.0)
                denominator = tf + BM25_K1 * (1.0 - BM25_B + BM25_B * dl / avgdl)
                score += idf * numerator / denominator
            scored.append((score, chunk_id, c["document_id"], c["text"],
                          c["document_key"], c["section_key"]))

        scored.sort(key=lambda x: x[0], reverse=True)

        return [
            TextSearchResult(
                chunk_id=chunk_id,
                document_id=doc_id,
                text=text,
                score=score,
                document_key=doc_key,
                section_key=sec_key,
            )
            for score, chunk_id, doc_id, text, doc_key, sec_key in scored[:top_k]
        ]

    async def get_chunk_ids_by_document(self, document_id: str) -> list[str]:
        """Return all chunk_ids for a given document_id from BM25."""
        await self._ensure_tables()
        rows = await self._query_param(
            f"SELECT chunk_id FROM {self._docs} WHERE document_id=:did",
            {"did": document_id},
        )
        return [r[0] for r in rows]

    async def delete_by_document(self, document_id: str) -> None:
        await self._ensure_tables()

        rows = await self._query_param(
            f"SELECT chunk_id FROM {self._docs} WHERE document_id = :did",
            {"did": document_id},
        )
        chunk_ids = [r[0] for r in rows]

        if not chunk_ids:
            return

        await self._increment_terms(chunk_ids, delta=-1)

        placeholders = ", ".join(f":c{i}" for i in range(len(chunk_ids)))
        params = {f"c{i}": cid for i, cid in enumerate(chunk_ids)}
        await self._batch_param([
            (f"DELETE FROM {self._idx} WHERE chunk_id IN ({placeholders})", params),
        ])
        await self._exec_param(
            f"DELETE FROM {self._docs} WHERE document_id = :did",
            {"did": document_id},
        )

        self._invalidate_cache()

    async def delete_by_chunks(self, chunk_ids: list[str]) -> None:
        await self._ensure_tables()
        if not chunk_ids:
            return
        await self._increment_terms(chunk_ids, delta=-1)

        placeholders = ", ".join(f":c{i}" for i in range(len(chunk_ids)))
        params = {f"c{i}": cid for i, cid in enumerate(chunk_ids)}
        await self._batch_param([
            (f"DELETE FROM {self._idx} WHERE chunk_id IN ({placeholders})", params),
            (f"DELETE FROM {self._docs} WHERE chunk_id IN ({placeholders})", dict(params)),
        ])

        self._invalidate_cache()

    async def count(self) -> int:
        await self._ensure_tables()
        rows = await self._query(f"SELECT COUNT(*) FROM {self._docs}")
        return rows[0][0] if rows else 0

    # ── Helper methods ───────────────────────────────────────────

    async def _increment_terms(self, chunk_ids: list[str], delta: int = -1) -> None:
        """Increment/decrement df for terms in given chunks by their actual count."""
        if not chunk_ids:
            return

        placeholders = ", ".join(f":c{i}" for i in range(len(chunk_ids)))
        params = {f"c{i}": cid for i, cid in enumerate(chunk_ids)}
        rows = await self._query_param(
            f"SELECT term, COUNT(DISTINCT chunk_id) as cnt FROM {self._idx} "
            f"WHERE chunk_id IN ({placeholders}) GROUP BY term",
            params,
        )
        if not rows:
            return

        stmts: list[tuple[str, dict]] = []
        for term, cnt in rows:
            if delta < 0:
                stmts.append((
                    f"UPDATE {self._stats} SET df = MAX(0, df + :d) "
                    f"WHERE term = :term",
                    {"d": delta * cnt, "term": term},
                ))
                stmts.append((
                    f"DELETE FROM {self._stats} WHERE term = :term AND df <= 0",
                    {"term": term},
                ))
            else:
                stmts.append((
                    f"INSERT INTO {self._stats} (term, df) VALUES (:term, 1) "
                    f"ON CONFLICT(term) DO UPDATE SET df = df + :d",
                    {"term": term, "d": delta},
                ))
        await self._batch_param(stmts)

    async def raw_query(self, sql: str) -> list[Any]:
        """Exposed for settings.py rebuild logic."""
        return await self._query(sql)
