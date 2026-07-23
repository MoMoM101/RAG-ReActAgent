import enum
from datetime import UTC, datetime

from sqlalchemy import JSON, Boolean, DateTime, Integer, String, Text
from sqlalchemy import Enum as SAEnum
from sqlalchemy.orm import Mapped, mapped_column

from .database import Base


class DocStatus(enum.StrEnum):
    uploaded = "uploaded"
    waiting_for_ocr = "waiting_for_ocr"
    parsing = "parsing"
    chunking = "chunking"
    embedding = "embedding"
    indexing = "indexing"
    ready = "ready"
    failed = "failed"


class GenerationStatus(enum.StrEnum):
    preparing = "preparing"
    writing_vector = "writing_vector"
    writing_bm25 = "writing_bm25"
    verifying = "verifying"
    committed = "committed"
    failed = "failed"


class Document(Base):
    __tablename__ = "documents"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    filename: Mapped[str] = mapped_column(String(500), nullable=False)
    file_hash: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    file_size: Mapped[int] = mapped_column(nullable=False)
    file_type: Mapped[str] = mapped_column(String(20), nullable=False)
    storage_key: Mapped[str | None] = mapped_column(String, nullable=True)
    status: Mapped[DocStatus] = mapped_column(SAEnum(DocStatus), default=DocStatus.uploaded, index=True)
    chunk_count: Mapped[int] = mapped_column(default=0)
    embedding_model: Mapped[str | None] = mapped_column(String(100), nullable=True)
    embedding_dim: Mapped[int | None] = mapped_column(nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(UTC))
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(UTC), onupdate=lambda: datetime.now(UTC))
    raw_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    chunk_size: Mapped[int | None] = mapped_column(Integer, nullable=True)
    active_generation_id: Mapped[str | None] = mapped_column(String(36), nullable=True)


class IndexGeneration(Base):
    __tablename__ = "index_generations"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    doc_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    status: Mapped[GenerationStatus] = mapped_column(
        SAEnum(GenerationStatus), default=GenerationStatus.preparing, index=True
    )
    expected_chunk_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    vector_chunk_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    bm25_chunk_count: Mapped[int | None] = mapped_column("bm25_count", Integer, nullable=True)
    chunk_ids_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    error_stage: Mapped[str | None] = mapped_column(String(50), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=lambda: datetime.now(UTC)
    )
    committed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class Conversation(Base):
    __tablename__ = "conversations"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    title: Mapped[str] = mapped_column(String(200), default="New Chat")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(UTC))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=lambda: datetime.now(UTC),
        onupdate=lambda: datetime.now(UTC), index=True,
    )
    last_extracted_at: Mapped[datetime] = mapped_column(DateTime, nullable=True)
    context_summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    context_summary_through_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    context_summary_source_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    context_summary_token_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    context_summary_version: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    context_summary_updated_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class Message(Base):
    __tablename__ = "messages"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    conversation_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    role: Mapped[str] = mapped_column(String(20), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=True)
    tool_call_id: Mapped[str] = mapped_column(String(100), nullable=True)
    tool_name: Mapped[str] = mapped_column(String(100), nullable=True)
    tool_args: Mapped[str] = mapped_column(Text, nullable=True)
    sources: Mapped[str] = mapped_column(Text, nullable=True)
    verification: Mapped[str | None] = mapped_column(Text, nullable=True)
    tool_result_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(UTC))


class UserMemory(Base):
    __tablename__ = "user_memories"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    memory_type: Mapped[str] = mapped_column(String(20), default="fact", nullable=False)
    deprecated: Mapped[bool] = mapped_column(Boolean, default=False)
    embedding_model: Mapped[str] = mapped_column(String(100), nullable=True)
    conversation_id: Mapped[str] = mapped_column(String(36), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(UTC))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=lambda: datetime.now(UTC),
        onupdate=lambda: datetime.now(UTC),
    )


class UserProfile(Base):
    __tablename__ = "user_profiles"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    profile_data: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    memory_ids: Mapped[list] = mapped_column(JSON, nullable=True, default=list)
    version: Mapped[int] = mapped_column(Integer, default=1)
    generated_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(UTC))


class UserRole(enum.StrEnum):
    viewer = "viewer"
    editor = "editor"
    knowledge_admin = "knowledge_admin"
    system_admin = "system_admin"


class User(Base):
    __tablename__ = "users"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    username: Mapped[str] = mapped_column(String(100), unique=True, nullable=False)
    password_hash: Mapped[str] = mapped_column(String(128), nullable=False)
    role: Mapped[UserRole] = mapped_column(SAEnum(UserRole), default=UserRole.viewer)
    disabled: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(UTC))
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
