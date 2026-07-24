"""Current supported SQLite schema baseline — hand-reviewed.

This is the authoritative schema.  Every table (ORM and raw-SQL), virtual
table (FTS5), BM25 index, and constraint the application needs must be
represented here.  Autogenerate cannot detect FTS5 virtual tables or
tables created via raw SQL in init_db(), so those are added manually.

Revision ID: 0001
Revises: None
Create Date: 2026-07-18
"""
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # ── ORM-managed tables ─────────────────────────────────────────

    op.create_table(
        "documents",
        sa.Column("id", sa.String(36), nullable=False),
        sa.Column("filename", sa.String(500), nullable=False),
        sa.Column("file_hash", sa.String(64), nullable=False, unique=True),
        sa.Column("file_size", sa.Integer(), nullable=False),
        sa.Column("file_type", sa.String(20), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("chunk_count", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("embedding_model", sa.String(100), nullable=True),
        sa.Column("embedding_dim", sa.Integer(), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("created_at", sa.String(), nullable=False),
        sa.Column("updated_at", sa.String(), nullable=False),
        sa.Column("raw_text", sa.Text(), nullable=True),
        sa.Column("chunk_size", sa.Integer(), nullable=True),
        sa.Column("active_generation_id", sa.String(36), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )

    op.create_table(
        "index_generations",
        sa.Column("id", sa.String(36), nullable=False),
        sa.Column("doc_id", sa.String(36), nullable=False),
        # Per schema inventory (Task A1), real production DBs have
        # DEFAULT 'staging' — not the ORM/server default 'preparing'.
        sa.Column("status", sa.String(50), nullable=False, server_default="staging"),
        sa.Column("expected_chunk_count", sa.Integer(), nullable=True),
        sa.Column("vector_chunk_count", sa.Integer(), nullable=True),
        sa.Column("bm25_count", sa.Integer(), nullable=True),
        sa.Column("chunk_ids_hash", sa.String(64), nullable=True),
        sa.Column("chunk_ids_consistent", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("error_stage", sa.String(50), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("created_at", sa.String(), nullable=False, server_default=sa.text("(datetime('now'))")),
        sa.Column("committed_at", sa.String(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )

    op.create_table(
        "conversations",
        sa.Column("id", sa.String(36), nullable=False),
        sa.Column("title", sa.String(200), nullable=True),
        sa.Column("created_at", sa.String(), nullable=False),
        sa.Column("updated_at", sa.String(), nullable=False),
        sa.Column("last_extracted_at", sa.String(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )

    op.create_table(
        "messages",
        sa.Column("id", sa.String(36), nullable=False),
        sa.Column("conversation_id", sa.String(36), nullable=False),
        sa.Column("role", sa.String(20), nullable=False),
        sa.Column("content", sa.Text(), nullable=True),
        sa.Column("tool_call_id", sa.String(100), nullable=True),
        sa.Column("tool_name", sa.String(100), nullable=True),
        sa.Column("tool_args", sa.Text(), nullable=True),
        sa.Column("sources", sa.Text(), nullable=True),
        sa.Column("verification", sa.Text(), nullable=True),
        sa.Column("tool_result_json", sa.Text(), nullable=True),
        sa.Column("created_at", sa.String(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )

    op.create_table(
        "user_memories",
        sa.Column("id", sa.String(36), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("memory_type", sa.String(20), nullable=False, server_default="fact"),
        sa.Column("deprecated", sa.Boolean(), nullable=False, server_default=sa.text("0")),
        sa.Column("embedding_model", sa.String(100), nullable=True),
        sa.Column("conversation_id", sa.String(36), nullable=True),
        sa.Column("created_at", sa.String(), nullable=False),
        sa.Column("updated_at", sa.String(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )

    op.create_table(
        "user_profiles",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("profile_data", sa.Text(), nullable=False),
        sa.Column("memory_ids", sa.Text(), nullable=True),
        sa.Column("version", sa.Integer(), nullable=False, server_default=sa.text("1")),
        sa.Column("generated_at", sa.String(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )

    op.create_table(
        "users",
        sa.Column("id", sa.String(36), nullable=False),
        sa.Column("username", sa.String(100), nullable=False),
        sa.Column("password_hash", sa.String(128), nullable=False),
        sa.Column("role", sa.String(20), nullable=False, server_default="viewer"),
        sa.Column("disabled", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("created_at", sa.String(), nullable=False, server_default=sa.text("(datetime('now'))")),
        sa.Column("last_login_at", sa.String(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("username"),
    )

    # ── Raw-SQL tables (managed outside ORM) ───────────────────────

    op.create_table(
        "task_queue",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("status", sa.String(), nullable=False, server_default="pending"),
        sa.Column("metadata", sa.Text(), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("heartbeat_at", sa.String(), nullable=True),
        sa.Column("created_at", sa.String(), nullable=False, server_default=sa.text("(datetime('now'))")),
        sa.Column("completed_at", sa.String(), nullable=True),
        sa.Column("task_type", sa.String(), nullable=True),
        sa.Column("payload_json", sa.Text(), nullable=True),
        sa.Column("idempotency_key", sa.String(), nullable=True),
        sa.Column("attempt", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("max_attempts", sa.Integer(), nullable=False, server_default=sa.text("3")),
        sa.Column("next_run_at", sa.String(), nullable=True),
        sa.Column("worker_id", sa.String(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )

    op.create_table(
        "audit_logs",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("actor_id", sa.String(), nullable=False),
        sa.Column("actor_username", sa.String(), nullable=False),
        sa.Column("action", sa.String(), nullable=False),
        sa.Column("object_type", sa.String(), nullable=True, server_default=""),
        sa.Column("object_id", sa.String(), nullable=True, server_default=""),
        sa.Column("result", sa.String(), nullable=False, server_default="success"),
        sa.Column("detail", sa.Text(), nullable=True, server_default=""),
        sa.Column("request_id", sa.String(), nullable=True, server_default=""),
        sa.Column("created_at", sa.String(), nullable=False, server_default=sa.text("(datetime('now'))")),
        sa.PrimaryKeyConstraint("id"),
    )

    # ── BM25 inverted-index tables (raw SQL, composite keys) ──────

    op.execute("""
        CREATE TABLE IF NOT EXISTS bm25_docs (
            chunk_id TEXT PRIMARY KEY,
            document_id TEXT NOT NULL,
            document_key TEXT NOT NULL DEFAULT '',
            section_key TEXT NOT NULL DEFAULT '',
            chunk_index INTEGER NOT NULL DEFAULT 0,
            text TEXT NOT NULL,
            token_count INTEGER NOT NULL DEFAULT 0
        )
    """)

    op.execute("""
        CREATE TABLE IF NOT EXISTS bm25_index (
            term TEXT NOT NULL,
            chunk_id TEXT NOT NULL,
            tf INTEGER NOT NULL,
            PRIMARY KEY (term, chunk_id)
        )
    """)

    op.execute("""
        CREATE TABLE IF NOT EXISTS bm25_stats (
            term TEXT PRIMARY KEY,
            df INTEGER NOT NULL DEFAULT 0
        )
    """)

    # ── Legacy FTS5 virtual table (kept for backward compatibility) ─

    op.execute("""
        CREATE VIRTUAL TABLE IF NOT EXISTS chunks_fts
        USING fts5(chunk_id, document_id, content, tokenize='trigram')
    """)

    # ── Indexes ────────────────────────────────────────────────────

    # documents
    op.create_index("idx_documents_status", "documents", ["status"])
    op.create_index("idx_documents_active_gen", "documents", ["active_generation_id"])

    # index_generations
    op.create_index("idx_index_generations_doc", "index_generations", ["doc_id"])
    op.create_index("idx_index_generations_status", "index_generations", ["status"])

    # conversations
    op.create_index("idx_conversations_updated", "conversations", ["updated_at"])

    # messages
    op.create_index("idx_messages_conversation", "messages", ["conversation_id"])
    op.create_index("idx_messages_created", "messages", ["created_at"])

    # task_queue
    op.create_index("idx_task_queue_idempotency", "task_queue", ["idempotency_key"])
    op.create_index("idx_task_queue_status_next", "task_queue", ["status", "next_run_at"])

    # audit_logs
    op.create_index("idx_audit_logs_action", "audit_logs", ["action"])
    op.create_index("idx_audit_logs_actor", "audit_logs", ["actor_id"])
    op.create_index("idx_audit_logs_created", "audit_logs", ["created_at"])

    # bm25_docs
    op.create_index("idx_bm25_docs_did", "bm25_docs", ["document_id"])

    # bm25_index
    op.create_index("idx_bm25_index_term", "bm25_index", ["term"])

    # ── WAL journal mode ───────────────────────────────────────────
    op.execute("PRAGMA journal_mode=WAL")


def downgrade() -> None:
    # Virtual / raw-SQL tables (drop before core tables)
    op.execute("DROP TABLE IF EXISTS chunks_fts")
    op.execute("DROP TABLE IF EXISTS bm25_stats")
    op.execute("DROP TABLE IF EXISTS bm25_index")
    op.execute("DROP TABLE IF EXISTS bm25_docs")

    op.drop_table("audit_logs")
    op.drop_table("task_queue")
    op.drop_table("users")
    op.drop_table("user_profiles")
    op.drop_table("user_memories")
    op.drop_table("messages")
    op.drop_table("conversations")
    op.drop_table("index_generations")
    op.drop_table("documents")
