from sqlalchemy import text as sa_text
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from sqlalchemy.orm import DeclarativeBase
from config import settings

engine = create_async_engine(settings.database_url, echo=False)
async_session = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


class Base(DeclarativeBase):
    pass


async def init_db():
    import models.orm  # noqa: ensure models loaded
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
        # FTS5 virtual table (not managed by SQLAlchemy ORM)
        await conn.execute(sa_text(
            "CREATE VIRTUAL TABLE IF NOT EXISTS chunks_fts "
            "USING fts5(chunk_id, document_id, content, tokenize='unicode61')"
        ))


async def get_db():
    async with async_session() as session:
        yield session
