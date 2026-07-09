from sqlalchemy import text as sa_text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase

from config import settings

engine = create_async_engine(settings.database_url, echo=False)
async_session = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


class Base(DeclarativeBase):
    pass


async def init_db():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        # 迁移：为旧 user_memories 表新增字段（先检查列是否存在）
        cols = (await conn.exec_driver_sql("PRAGMA table_info(user_memories)")).fetchall()
        existing = {row[1] for row in cols}
        for col, spec in [
            ("memory_type", "TEXT NOT NULL DEFAULT 'fact'"),
            ("deprecated", "INTEGER NOT NULL DEFAULT 0"),
            ("updated_at", "TIMESTAMP"),
        ]:
            if col not in existing:
                await conn.exec_driver_sql(
                    f"ALTER TABLE user_memories ADD COLUMN {col} {spec}"
                )
        # 迁移：为 conversations 表新增 last_extracted_at
        conv_cols = (await conn.exec_driver_sql("PRAGMA table_info(conversations)")).fetchall()
        conv_existing = {row[1] for row in conv_cols}
        if "last_extracted_at" not in conv_existing:
            await conn.exec_driver_sql(
                "ALTER TABLE conversations ADD COLUMN last_extracted_at TIMESTAMP"
            )
        # 迁移: documents 表新增 raw_text、chunk_size 列
        doc_cols = (await conn.exec_driver_sql("PRAGMA table_info(documents)")).fetchall()
        doc_existing = {row[1] for row in doc_cols}
        for col, spec in [
            ("raw_text", "TEXT"),
            ("chunk_size", "INTEGER"),
        ]:
            if col not in doc_existing:
                await conn.exec_driver_sql(
                    f"ALTER TABLE documents ADD COLUMN {col} {spec}"
                )
        await conn.exec_driver_sql("PRAGMA journal_mode=WAL")
        await conn.exec_driver_sql("PRAGMA busy_timeout=30000")
        await conn.exec_driver_sql("PRAGMA foreign_keys=ON")
        # Legacy FTS5 table (deprecated, replaced by bm25_* tables below)
        # Kept for backward compatibility — no longer used for search
        await conn.execute(sa_text(
            "CREATE VIRTUAL TABLE IF NOT EXISTS chunks_fts "
            "USING fts5(chunk_id, document_id, content, tokenize='trigram')"
        ))
        # 检测已有表的 tokenizer，如果与 trigram 不兼容则重建
        fts_info = (await conn.exec_driver_sql(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name='chunks_fts'"
        )).fetchone()
        if fts_info and fts_info[0] and "trigram" not in fts_info[0]:
            import logging as _logging
            _log = _logging.getLogger(__name__)
            _log.warning(
                "FTS5 tokenizer migrated from unicode61 to trigram; "
                "old index dropped. Re-upload documents or use rebuild to restore keyword search."
            )
            await conn.exec_driver_sql("DROP TABLE IF EXISTS chunks_fts")
            await conn.execute(sa_text(
                "CREATE VIRTUAL TABLE chunks_fts "
                "USING fts5(chunk_id, document_id, content, tokenize='trigram')"
            ))

        # BM25 inverted index tables (replaces FTS5 for keyword search)
        for stmt in [
            "CREATE TABLE IF NOT EXISTS bm25_docs ("
            "  chunk_id TEXT PRIMARY KEY, document_id TEXT NOT NULL,"
            "  text TEXT NOT NULL, token_count INTEGER NOT NULL DEFAULT 0)",
            "CREATE TABLE IF NOT EXISTS bm25_index ("
            "  term TEXT NOT NULL, chunk_id TEXT NOT NULL, tf INTEGER NOT NULL,"
            "  PRIMARY KEY (term, chunk_id))",
            "CREATE TABLE IF NOT EXISTS bm25_stats ("
            "  term TEXT PRIMARY KEY, df INTEGER NOT NULL DEFAULT 0)",
            "CREATE INDEX IF NOT EXISTS idx_bm25_docs_did ON bm25_docs(document_id)",
            "CREATE INDEX IF NOT EXISTS idx_bm25_index_term ON bm25_index(term)",
        ]:
            await conn.exec_driver_sql(stmt)


async def get_db():
    async with async_session() as session:
        yield session
