from sqlalchemy import text as sa_text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase

from config import settings

engine = create_async_engine(settings.database_url, echo=False)
async_session = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


class Base(DeclarativeBase):
    pass


async def init_db():
    # Ensure all ORM models are registered with Base.metadata before create_all
    import models.orm  # noqa: F401

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
        # 迁移: messages 表新增 tool_result_json 列
        msg_cols = (await conn.exec_driver_sql("PRAGMA table_info(messages)")).fetchall()
        msg_existing = {row[1] for row in msg_cols}
        if "tool_result_json" not in msg_existing:
            await conn.exec_driver_sql(
                "ALTER TABLE messages ADD COLUMN tool_result_json TEXT"
            )
        if "verification" not in msg_existing:
            await conn.exec_driver_sql(
                "ALTER TABLE messages ADD COLUMN verification TEXT"
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
            "  document_key TEXT NOT NULL DEFAULT '',"
            "  section_key TEXT NOT NULL DEFAULT '',"
            "  chunk_index INTEGER NOT NULL DEFAULT 0,"
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

        # 迁移: bm25_docs 新增 document_key, section_key, chunk_index
        bm25_cols = (await conn.exec_driver_sql("PRAGMA table_info(bm25_docs)")).fetchall()
        bm25_existing = {row[1] for row in bm25_cols}
        for col, spec in [
            ("document_key", "TEXT NOT NULL DEFAULT ''"),
            ("section_key", "TEXT NOT NULL DEFAULT ''"),
            ("chunk_index", "INTEGER NOT NULL DEFAULT 0"),
        ]:
            if col not in bm25_existing:
                await conn.exec_driver_sql(
                    f"ALTER TABLE bm25_docs ADD COLUMN {col} {spec}"
                )

        # Generation tracking for cross-store atomic indexing
        await conn.exec_driver_sql(
            "CREATE TABLE IF NOT EXISTS index_generations ("
            "  id TEXT PRIMARY KEY,"
            "  doc_id TEXT NOT NULL,"
            "  status TEXT NOT NULL DEFAULT 'preparing',"
            "  vector_chunk_count INTEGER,"
            "  bm25_count INTEGER,"
            "  chunk_ids_hash TEXT,"
            "  chunk_ids_consistent INTEGER NOT NULL DEFAULT 0,"
            "  created_at TEXT NOT NULL DEFAULT (datetime('now')),"
            "  committed_at TEXT"
            ")"
        )

        # Persistent task queue for restart recovery
        await conn.exec_driver_sql(
            "CREATE TABLE IF NOT EXISTS task_queue ("
            "  id TEXT PRIMARY KEY,"
            "  name TEXT NOT NULL,"
            "  status TEXT NOT NULL DEFAULT 'pending',"  # pending | running | done | failed
            "  metadata TEXT,"
            "  error TEXT,"
            "  heartbeat_at TEXT,"
            "  created_at TEXT NOT NULL DEFAULT (datetime('now')),"
            "  completed_at TEXT"
            ")"
        )

        # Migration: task_queue extended columns for idempotent replay
        tq_cols = (await conn.exec_driver_sql("PRAGMA table_info(task_queue)")).fetchall()
        tq_existing = {row[1] for row in tq_cols}
        for col, spec in [
            ("task_type", "TEXT"),
            ("payload_json", "TEXT"),
            ("idempotency_key", "TEXT"),
            ("attempt", "INTEGER NOT NULL DEFAULT 0"),
            ("max_attempts", "INTEGER NOT NULL DEFAULT 3"),
            ("next_run_at", "TEXT"),
            ("worker_id", "TEXT"),
        ]:
            if col not in tq_existing:
                await conn.exec_driver_sql(
                    f"ALTER TABLE task_queue ADD COLUMN {col} {spec}"
                )
        await conn.exec_driver_sql(
            "CREATE INDEX IF NOT EXISTS idx_task_queue_idempotency "
            "ON task_queue(idempotency_key)"
        )
        await conn.exec_driver_sql(
            "CREATE INDEX IF NOT EXISTS idx_task_queue_status_next "
            "ON task_queue(status, next_run_at)"
        )

        # Migration: rename index_generations columns (old -> new names)
        gen_cols_all = (await conn.exec_driver_sql("PRAGMA table_info(index_generations)")).fetchall()
        gen_existing_all = {row[1] for row in gen_cols_all}
        if "qdrant_count" in gen_existing_all and "vector_chunk_count" not in gen_existing_all:
            await conn.exec_driver_sql(
                "ALTER TABLE index_generations ADD COLUMN vector_chunk_count INTEGER"
            )
        if "chunk_ids_consistent" in gen_existing_all and "chunk_ids_hash" not in gen_existing_all:
            await conn.exec_driver_sql(
                "ALTER TABLE index_generations ADD COLUMN chunk_ids_hash TEXT"
            )

        # Migration: index_generations new columns for atomic visibility
        gen_cols = (await conn.exec_driver_sql("PRAGMA table_info(index_generations)")).fetchall()
        gen_existing = {row[1] for row in gen_cols}
        for col, spec in [
            ("expected_chunk_count", "INTEGER"),
            ("vector_chunk_count", "INTEGER"),
            ("chunk_ids_hash", "TEXT"),
            ("chunk_ids_consistent", "INTEGER NOT NULL DEFAULT 0"),
            ("error_stage", "TEXT"),
            ("error_message", "TEXT"),
        ]:
            if col not in gen_existing:
                await conn.exec_driver_sql(
                    f"ALTER TABLE index_generations ADD COLUMN {col} {spec}"
                )
        # Migration: documents.active_generation_id
        doc_cols2 = (await conn.exec_driver_sql("PRAGMA table_info(documents)")).fetchall()
        doc_existing2 = {row[1] for row in doc_cols2}
        if "active_generation_id" not in doc_existing2:
            await conn.exec_driver_sql(
                "ALTER TABLE documents ADD COLUMN active_generation_id TEXT"
            )

        # Migration: users table
        await conn.exec_driver_sql(
            "CREATE TABLE IF NOT EXISTS users ("
            "  id TEXT PRIMARY KEY,"
            "  username TEXT NOT NULL UNIQUE,"
            "  password_hash TEXT NOT NULL,"
            "  role TEXT NOT NULL DEFAULT 'viewer',"
            "  disabled INTEGER NOT NULL DEFAULT 0,"
            "  created_at TEXT NOT NULL DEFAULT (datetime('now')),"
            "  last_login_at TEXT"
            ")"
        )

        # Migration: audit_logs table
        await conn.exec_driver_sql(
            "CREATE TABLE IF NOT EXISTS audit_logs ("
            "  id INTEGER PRIMARY KEY AUTOINCREMENT,"
            "  actor_id TEXT NOT NULL,"
            "  actor_username TEXT NOT NULL,"
            "  action TEXT NOT NULL,"
            "  object_type TEXT DEFAULT '',"
            "  object_id TEXT DEFAULT '',"
            "  result TEXT NOT NULL DEFAULT 'success',"
            "  detail TEXT DEFAULT '',"
            "  request_id TEXT DEFAULT '',"
            "  created_at TEXT NOT NULL DEFAULT (datetime('now'))"
            ")"
        )


async def get_db():
    async with async_session() as session:
        yield session
