cl# RAG Agent MVP Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build MVP document RAG agent — upload docs, parse/chunk/embed, hybrid retrieval (Qdrant + FTS5 + RRF), SSE chat with source citations.

**Architecture:** FastAPI backend with self-built ReAct Agent loop + LlamaIndex RAG pipeline. React frontend with SSE streaming, chat panel, and document management. Qdrant for vectors, SQLite FTS5 for BM25, SQLite for metadata.

**Tech Stack:** Python 3.11+ / FastAPI / LlamaIndex / Qdrant / SQLite FTS5 / React 18 / TypeScript / Vite / Zustand / Tailwind CSS

**Scope (MVP):** Document upload → parse → chunk → embed → Qdrant + FTS5 dual-write. Hybrid retrieval (semantic + BM25 + RRF). SSE chat with tool calling (search_docs only). Source citations. Frontend chat + document list. No long memory, no OCR multi-engine, no web search, no calculator, no settings center.

---

## File Structure

### Backend (new files)

```
backend/
├── main.py                          # FastAPI app, CORS, startup
├── config.py                        # Settings from env
├── api/
│   ├── __init__.py
│   ├── chat.py                      # POST /api/chat (SSE)
│   ├── documents.py                 # CRUD /api/documents
│   └── conversations.py            # CRUD /api/conversations
├── agent/
│   ├── __init__.py
│   ├── loop.py                      # ReAct Loop: Thought→ToolCall→Observe
│   ├── classifier.py               # Intent routing hint (soft)
│   └── context.py                  # Context window manager
├── rag/
│   ├── __init__.py
│   ├── pipeline.py                 # Ingestion pipeline orchestrator
│   ├── loaders.py                  # PDF/DOCX/TXT/MD/CSV/XLSX loaders
│   ├── splitter.py                 # Token-based chunk splitter
│   └── retriever.py               # Hybrid retriever: Qdrant + FTS5 + RRF
├── vectordb/
│   ├── __init__.py
│   ├── base.py                     # Abstract vector DB interface
│   ├── qdrant.py                   # Qdrant implementation
│   └── factory.py                  # Create vector DB from config
├── textdb/
│   ├── __init__.py
│   ├── base.py                     # Abstract full-text search interface
│   └── sqlite_fts.py              # SQLite FTS5 implementation
├── embedding/
│   ├── __init__.py
│   ├── base.py                     # Abstract embedding interface
│   ├── openai_embed.py            # OpenAI embedding
│   └── factory.py                 # Create embedding from config
├── llm/
│   ├── __init__.py
│   ├── base.py                     # Abstract LLM interface
│   ├── openai_llm.py              # OpenAI LLM
│   └── factory.py                 # Create LLM from config
├── models/
│   ├── __init__.py
│   ├── schemas.py                 # Pydantic models
│   └── database.py               # SQLite setup, migrations, helpers
├── storage/
│   ├── __init__.py
│   └── files.py                   # File save/delete on disk
└── worker/
    ├── __init__.py
    └── ingestion.py               # Background ingestion worker
```

### Frontend (new files)

```
frontend/
├── index.html
├── package.json
├── vite.config.ts
├── tsconfig.json
├── tailwind.config.ts
├── postcss.config.js
├── src/
│   ├── main.tsx
│   ├── App.tsx
│   ├── App.css
│   ├── api/
│   │   ├── client.ts              # Fetch wrapper, base URL
│   │   ├── chat.ts                # SSE connection helper
│   │   ├── documents.ts           # Document CRUD API calls
│   │   └── conversations.ts      # Conversation API calls
│   ├── stores/
│   │   ├── chatStore.ts           # Messages, SSE state, sendMessage
│   │   └── documentStore.ts      # Document list, upload state
│   ├── components/
│   │   ├── layout/
│   │   │   ├── Sidebar.tsx
│   │   │   └── MainLayout.tsx
│   │   ├── chat/
│   │   │   ├── ChatPanel.tsx
│   │   │   ├── MessageList.tsx
│   │   │   ├── MessageBubble.tsx
│   │   │   ├── ToolCallCard.tsx
│   │   │   ├── SourceCard.tsx
│   │   │   ├── ClarifyBubble.tsx
│   │   │   └── ChatInput.tsx
│   │   ├── documents/
│   │   │   ├── DocumentList.tsx
│   │   │   ├── DocumentCard.tsx
│   │   │   ├── UploadZone.tsx
│   │   │   └── ChunkViewer.tsx
│   │   └── shared/
│   │       ├── Loading.tsx
│   │       └── ErrorBoundary.tsx
│   ├── hooks/
│   │   ├── useSSE.ts
│   │   └── useFileUpload.ts
│   └── types/
│       ├── chat.ts
│       ├── document.ts
│       └── index.ts
```

---

## Task 1: Project Scaffold & Dependencies

**Files:**
- Create: `backend/requirements.txt`
- Create: `backend/main.py`
- Create: `backend/config.py`
- Create: `backend/models/__init__.py`
- Create: `backend/models/database.py`
- Create: `backend/models/schemas.py`

- [ ] **Step 1: Create requirements.txt**

Write `backend/requirements.txt`:
```
fastapi==0.115.6
uvicorn[standard]==0.34.0
python-multipart==0.0.20
llama-index-core==0.12.30
llama-index-readers-file==0.4.5
llama-index-embeddings-openai==0.3.1
llama-index-vector-stores-qdrant==0.4.1
qdrant-client==1.13.0
pymupdf==1.25.3
python-docx==1.1.2
openpyxl==3.1.5
pandas==2.2.3
tiktoken==0.8.0
openai==1.70.0
pydantic==2.10.5
pydantic-settings==2.7.1
python-dotenv==1.0.1
sqlalchemy==2.0.36
aiosqlite==0.20.0
sse-starlette==2.2.1
```

- [ ] **Step 2: Install dependencies**

Run: `cd backend && pip install -r requirements.txt`
Expected: All packages install successfully.

- [ ] **Step 3: Create config.py**

Write `backend/config.py`:
```python
from pydantic_settings import BaseSettings

class Settings(BaseSettings):
    # LLM
    llm_provider: str = "openai"
    llm_model: str = "gpt-4o"
    llm_api_key: str = ""
    llm_base_url: str = "https://api.openai.com/v1"

    # Embedding
    embedding_provider: str = "openai"
    embedding_model: str = "text-embedding-3-small"
    embedding_api_key: str = ""
    embedding_base_url: str = "https://api.openai.com/v1"
    embedding_dim: int = 1536

    # Qdrant
    qdrant_host: str = "localhost"
    qdrant_port: int = 6333
    qdrant_collection: str = "rag_chunks"

    # SQLite
    database_url: str = "sqlite+aiosqlite:///./data/rag_agent.db"

    # Storage
    upload_dir: str = "./data/uploads"

    # Agent
    max_loop_iterations: int = 10
    max_tool_retries: int = 3
    max_total_time: int = 120
    chunk_size: int = 512
    chunk_overlap: int = 50
    retrieval_top_k: int = 5

    # Server
    secret_key: str = "change-me-in-production"

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8"}

settings = Settings()
```

- [ ] **Step 4: Create database.py**

Write `backend/models/database.py`:
```python
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from sqlalchemy.orm import DeclarativeBase
from config import settings

engine = create_async_engine(settings.database_url, echo=False)
async_session = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

class Base(DeclarativeBase):
    pass

async def init_db():
    import models.schemas  # noqa: ensure models loaded
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        # Enable WAL mode for FTS5 concurrency
        await conn.exec_driver_sql("PRAGMA journal_mode=WAL")
        await conn.exec_driver_sql("PRAGMA busy_timeout=30000")

async def get_db():
    async with async_session() as session:
        yield session
```

- [ ] **Step 5: Create schemas.py**

Write `backend/models/schemas.py`:
```python
from datetime import datetime
from sqlalchemy import Column, Integer, String, Text, DateTime, Float, Enum as SAEnum
from sqlalchemy.orm import Mapped, mapped_column
from .database import Base
import enum

class DocStatus(str, enum.Enum):
    uploaded = "uploaded"
    parsing = "parsing"
    chunking = "chunking"
    embedding = "embedding"
    indexing = "indexing"
    ready = "ready"
    failed = "failed"

class Document(Base):
    __tablename__ = "documents"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    filename: Mapped[str] = mapped_column(String(500), nullable=False)
    file_hash: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    file_size: Mapped[int] = mapped_column(Integer, nullable=False)
    file_type: Mapped[str] = mapped_column(String(20), nullable=False)
    status: Mapped[DocStatus] = mapped_column(SAEnum(DocStatus), default=DocStatus.uploaded)
    chunk_count: Mapped[int] = mapped_column(Integer, default=0)
    embedding_model: Mapped[str] = mapped_column(String(100), nullable=True)
    embedding_dim: Mapped[int] = mapped_column(Integer, nullable=True)
    error_message: Mapped[str] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

class Conversation(Base):
    __tablename__ = "conversations"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    title: Mapped[str] = mapped_column(String(200), default="New Chat")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

class Message(Base):
    __tablename__ = "messages"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    conversation_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    role: Mapped[str] = mapped_column(String(20), nullable=False)  # user / assistant / tool
    content: Mapped[str] = mapped_column(Text, nullable=True)
    tool_name: Mapped[str] = mapped_column(String(100), nullable=True)
    tool_args: Mapped[str] = mapped_column(Text, nullable=True)
    sources: Mapped[str] = mapped_column(Text, nullable=True)  # JSON array
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
```

The FTS5 virtual table will be created in Task 2 since it needs custom DDL.

- [ ] **Step 6: Create main.py skeleton**

Write `backend/main.py`:
```python
import os
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from models.database import init_db


@asynccontextmanager
async def lifespan(app: FastAPI):
    os.makedirs("data/uploads", exist_ok=True)
    await init_db()
    yield

app = FastAPI(title="RAG Agent", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/api/health")
async def health():
    return {"status": "ok"}
```

- [ ] **Step 7: Test startup**

Run: `cd backend && uvicorn main:app --reload`
Expected: Server starts, `GET http://localhost:8000/api/health` returns `{"status":"ok"}`. Stop server after.

- [ ] **Step 8: Commit**

```bash
cd backend
git add requirements.txt main.py config.py models/
git commit -m "feat: scaffold backend with FastAPI, SQLAlchemy, config, models"
```

---

## Task 2: FTS5 Full-Text Index Setup

**Files:**
- Create: `backend/textdb/__init__.py`
- Create: `backend/textdb/base.py`
- Create: `backend/textdb/sqlite_fts.py`
- Modify: `backend/models/database.py`

- [ ] **Step 1: Create FTS base interface**

Write `backend/textdb/base.py`:
```python
from abc import ABC, abstractmethod
from dataclasses import dataclass

@dataclass
class TextSearchResult:
    chunk_id: str
    document_id: str
    text: str
    score: float  # BM25 score

class BaseTextDB(ABC):
    @abstractmethod
    async def insert(self, chunk_id: str, document_id: str, text: str) -> None: ...

    @abstractmethod
    async def search(self, query: str, top_k: int = 10) -> list[TextSearchResult]: ...

    @abstractmethod
    async def delete_by_document(self, document_id: str) -> None: ...

    @abstractmethod
    async def delete_by_chunks(self, chunk_ids: list[str]) -> None: ...

    @abstractmethod
    async def count(self) -> int: ...
```

- [ ] **Step 2: Implement SQLite FTS5 backend**

Write `backend/textdb/sqlite_fts.py`:
```python
from sqlalchemy import text
from models.database import engine, async_session
from .base import BaseTextDB, TextSearchResult


class SQLiteFTS5(BaseTextDB):
    TABLE = "chunks_fts"

    @staticmethod
    async def create_table():
        async with engine.begin() as conn:
            await conn.execute(text(
                f"CREATE VIRTUAL TABLE IF NOT EXISTS {SQLiteFTS5.TABLE} "
                "USING fts5(chunk_id, document_id, content, tokenize='unicode61')"
            ))

    async def insert(self, chunk_id: str, document_id: str, text: str) -> None:
        escaped = text.replace("'", "''")
        async with async_session() as session:
            await session.execute(text(
                f"INSERT INTO {self.TABLE} (chunk_id, document_id, content) "
                f"VALUES ('{chunk_id}', '{document_id}', '{escaped}')"
            ))
            await session.commit()

    async def search(self, query: str, top_k: int = 10) -> list[TextSearchResult]:
        escaped = query.replace("'", "''")
        async with async_session() as session:
            result = await session.execute(text(
                f"SELECT chunk_id, document_id, content, bm25({self.TABLE}) as score "
                f"FROM {self.TABLE} WHERE {self.TABLE} MATCH '{escaped}' "
                f"ORDER BY score LIMIT {top_k}"
            ))
            rows = result.fetchall()
            return [
                TextSearchResult(
                    chunk_id=row[0],
                    document_id=row[1],
                    text=row[2],
                    score=float(row[3]) if row[3] is not None else 0.0,
                )
                for row in rows
            ]

    async def delete_by_document(self, document_id: str) -> None:
        async with async_session() as session:
            await session.execute(text(
                f"DELETE FROM {self.TABLE} WHERE document_id = '{document_id}'"
            ))
            await session.commit()

    async def delete_by_chunks(self, chunk_ids: list[str]) -> None:
        if not chunk_ids:
            return
        ids = "','".join(chunk_ids)
        async with async_session() as session:
            await session.execute(text(
                f"DELETE FROM {self.TABLE} WHERE chunk_id IN ('{ids}')"
            ))
            await session.commit()

    async def count(self) -> int:
        async with async_session() as session:
            result = await session.execute(text(f"SELECT COUNT(*) FROM {self.TABLE}"))
            return result.scalar() or 0
```

- [ ] **Step 3: Update init_db to create FTS5 table**

Modify `backend/models/database.py` — update `init_db`:
```python
from sqlalchemy import text as sa_text

async def init_db():
    import models.schemas  # noqa
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        await conn.exec_driver_sql("PRAGMA journal_mode=WAL")
        await conn.exec_driver_sql("PRAGMA busy_timeout=30000")
        # FTS5 virtual table (not managed by SQLAlchemy ORM)
        await conn.execute(sa_text(
            "CREATE VIRTUAL TABLE IF NOT EXISTS chunks_fts "
            "(chunk_id, document_id, content, tokenize='unicode61')"
        ))
```

- [ ] **Step 4: Write test**

Create `backend/tests/textdb/test_sqlite_fts.py`:
```python
import pytest
import asyncio
from textdb.sqlite_fts import SQLiteFTS5

@pytest.mark.asyncio
async def test_insert_and_search():
    fts = SQLiteFTS5()
    await fts.insert("chunk_1", "doc_1", "项目部署需要配置环境变量和数据库连接")
    await fts.insert("chunk_2", "doc_1", "数据库连接需要设置用户名和密码")
    await fts.insert("chunk_3", "doc_2", "前端使用 React 和 TypeScript")

    results = await fts.search("部署", top_k=5)
    assert len(results) >= 1
    assert results[0].chunk_id == "chunk_1"

    results = await fts.search("数据库连接", top_k=5)
    assert len(results) >= 1

    await fts.delete_by_document("doc_1")
    count = await fts.count()
    assert count == 1  # only doc_2 remains

@pytest.mark.asyncio
async def test_delete_by_chunks():
    fts = SQLiteFTS5()
    await fts.insert("ch_a", "doc_x", "text a")
    await fts.insert("ch_b", "doc_x", "text b")
    await fts.insert("ch_c", "doc_x", "text c")

    await fts.delete_by_chunks(["ch_a", "ch_c"])
    count = await fts.count()
    assert count == 1
```

- [ ] **Step 5: Run tests**

Run: `cd backend && pytest tests/textdb/test_sqlite_fts.py -v`
Expected: Both tests pass.

- [ ] **Step 6: Commit**

```bash
git add backend/textdb/ backend/tests/textdb/ backend/models/database.py
git commit -m "feat: add SQLite FTS5 full-text search with abstract interface"
```

---

## Task 3: Qdrant Vector Database Module

**Files:**
- Create: `backend/vectordb/__init__.py`
- Create: `backend/vectordb/base.py`
- Create: `backend/vectordb/qdrant.py`
- Create: `backend/vectordb/factory.py`

- [ ] **Step 1: Create base interface**

Write `backend/vectordb/base.py`:
```python
from abc import ABC, abstractmethod
from dataclasses import dataclass

@dataclass
class VectorSearchResult:
    chunk_id: str
    document_id: str
    text: str
    score: float

class BaseVectorDB(ABC):
    @abstractmethod
    async def upsert(self, points: list[dict]) -> None:
        """points: [{"id": str, "vector": list[float], "payload": dict}]"""
        ...

    @abstractmethod
    async def search(self, vector: list[float], top_k: int = 10) -> list[VectorSearchResult]: ...

    @abstractmethod
    async def delete_by_document(self, document_id: str) -> None: ...

    @abstractmethod
    async def delete_by_chunks(self, chunk_ids: list[str]) -> None: ...

    @abstractmethod
    async def collection_exists(self) -> bool: ...

    @abstractmethod
    async def create_collection(self, vector_size: int) -> None: ...
```

- [ ] **Step 2: Implement Qdrant backend**

Write `backend/vectordb/qdrant.py`:
```python
from qdrant_client import QdrantClient
from qdrant_client.models import Distance, VectorParams, PointStruct, Filter, FieldCondition, MatchValue
from config import settings
from .base import BaseVectorDB, VectorSearchResult


class QdrantVectorDB(BaseVectorDB):
    def __init__(self):
        self.client = QdrantClient(host=settings.qdrant_host, port=settings.qdrant_port)
        self.collection = settings.qdrant_collection

    async def collection_exists(self) -> bool:
        collections = self.client.get_collections()
        return any(c.name == self.collection for c in collections.collections)

    async def create_collection(self, vector_size: int) -> None:
        self.client.create_collection(
            collection_name=self.collection,
            vectors_config=VectorParams(size=vector_size, distance=Distance.COSINE),
        )

    async def upsert(self, points: list[dict]) -> None:
        qdrant_points = [
            PointStruct(
                id=p["id"],
                vector=p["vector"],
                payload=p.get("payload", {}),
            )
            for p in points
        ]
        self.client.upsert(collection_name=self.collection, points=qdrant_points)

    async def search(self, vector: list[float], top_k: int = 10) -> list[VectorSearchResult]:
        results = self.client.search(
            collection_name=self.collection,
            query_vector=vector,
            limit=top_k,
        )
        return [
            VectorSearchResult(
                chunk_id=r.id,
                document_id=r.payload.get("document_id", ""),
                text=r.payload.get("text", ""),
                score=r.score,
            )
            for r in results
        ]

    async def delete_by_document(self, document_id: str) -> None:
        self.client.delete(
            collection_name=self.collection,
            points_selector=Filter(
                must=[FieldCondition(key="document_id", match=MatchValue(value=document_id))]
            ),
        )

    async def delete_by_chunks(self, chunk_ids: list[str]) -> None:
        if not chunk_ids:
            return
        self.client.delete(
            collection_name=self.collection,
            points_selector=Filter(
                must=[FieldCondition(key="chunk_id", match=MatchValue(value=...))]  # handled via ids
            ),
        )
        # Qdrant supports point IDs deletion directly:
        self.client.delete(
            collection_name=self.collection,
            points_selector=chunk_ids,
        )
```

- [ ] **Step 3: Create factory**

Write `backend/vectordb/factory.py`:
```python
from config import settings
from .base import BaseVectorDB
from .qdrant import QdrantVectorDB

async def create_vectordb() -> BaseVectorDB:
    db = QdrantVectorDB()
    if not await db.collection_exists():
        await db.create_collection(settings.embedding_dim)
    return db
```

- [ ] **Step 4: Write test**

Create `backend/tests/vectordb/test_qdrant.py`:
```python
import pytest
from vectordb.qdrant import QdrantVectorDB

@pytest.mark.asyncio
async def test_upsert_and_search():
    db = QdrantVectorDB()
    if not await db.collection_exists():
        await db.create_collection(4)

    await db.upsert([
        {"id": "chunk_x", "vector": [1.0, 0.0, 0.0, 0.0],
         "payload": {"document_id": "doc_1", "text": "部署流程说明"}},
        {"id": "chunk_y", "vector": [0.0, 1.0, 0.0, 0.0],
         "payload": {"document_id": "doc_1", "text": "数据库配置"}},
    ])

    results = await db.search([1.0, 0.1, 0.0, 0.0], top_k=2)
    assert len(results) == 2
    assert results[0].chunk_id == "chunk_x"

    await db.delete_by_chunks(["chunk_x", "chunk_y"])
```

- [ ] **Step 5: Run test**

Run: `cd backend && pytest tests/vectordb/test_qdrant.py -v`
Expected: Test passes (requires Qdrant running on localhost:6333).

- [ ] **Step 6: Commit**

```bash
git add backend/vectordb/ backend/tests/vectordb/
git commit -m "feat: add Qdrant vector DB module with abstract interface"
```

---

## Task 4: Embedding Module

**Files:**
- Create: `backend/embedding/__init__.py`
- Create: `backend/embedding/base.py`
- Create: `backend/embedding/openai_embed.py`
- Create: `backend/embedding/factory.py`

- [ ] **Step 1: Create base interface**

Write `backend/embedding/base.py`:
```python
from abc import ABC, abstractmethod

class BaseEmbedding(ABC):
    @abstractmethod
    async def embed(self, texts: list[str]) -> list[list[float]]:
        """Embed a list of texts, return list of vectors."""
        ...

    @abstractmethod
    async def embed_query(self, text: str) -> list[float]:
        """Embed a single query text."""
        ...
```

- [ ] **Step 2: Implement OpenAI embedding**

Write `backend/embedding/openai_embed.py`:
```python
from openai import AsyncOpenAI
from config import settings
from .base import BaseEmbedding


class OpenAIEmbedding(BaseEmbedding):
    def __init__(self):
        self.client = AsyncOpenAI(
            api_key=settings.embedding_api_key or settings.llm_api_key,
            base_url=settings.embedding_base_url or settings.llm_base_url,
        )
        self.model = settings.embedding_model

    async def embed(self, texts: list[str]) -> list[list[float]]:
        response = await self.client.embeddings.create(
            model=self.model,
            input=texts,
        )
        return [d.embedding for d in response.data]

    async def embed_query(self, text: str) -> list[float]:
        results = await self.embed([text])
        return results[0]
```

- [ ] **Step 3: Create factory**

Write `backend/embedding/factory.py`:
```python
from config import settings
from .base import BaseEmbedding
from .openai_embed import OpenAIEmbedding

def create_embedding() -> BaseEmbedding:
    if settings.embedding_provider == "openai":
        return OpenAIEmbedding()
    raise ValueError(f"Unsupported embedding provider: {settings.embedding_provider}")
```

- [ ] **Step 4: Write test**

Create `backend/tests/embedding/test_openai_embed.py`:
```python
import pytest
from embedding.openai_embed import OpenAIEmbedding

@pytest.mark.asyncio
async def test_embed_single():
    emb = OpenAIEmbedding()
    result = await emb.embed(["测试文本"])
    assert len(result) == 1
    assert len(result[0]) > 0

@pytest.mark.asyncio
async def test_embed_query():
    emb = OpenAIEmbedding()
    vec = await emb.embed_query("什么是部署流程")
    assert len(vec) > 0
```

- [ ] **Step 5: Run test**

Run: `cd backend && pytest tests/embedding/test_openai_embed.py -v`
Expected: Pass (requires OPENAI_API_KEY env var).

- [ ] **Step 6: Commit**

```bash
git add backend/embedding/ backend/tests/embedding/
git commit -m "feat: add embedding module with OpenAI implementation"
```

---

## Task 5: LLM Module

**Files:**
- Create: `backend/llm/__init__.py`
- Create: `backend/llm/base.py`
- Create: `backend/llm/openai_llm.py`
- Create: `backend/llm/factory.py`

- [ ] **Step 1: Create base interface**

Write `backend/llm/base.py`:
```python
from abc import ABC, abstractmethod
from dataclasses import dataclass, field

@dataclass
class ToolCall:
    name: str
    arguments: dict

@dataclass
class LLMResponse:
    content: str | None = None
    tool_calls: list[ToolCall] = field(default_factory=list)
    is_final: bool = True  # False when asking for clarification

@dataclass
class ChatMessage:
    role: str  # system / user / assistant / tool
    content: str | None = None
    tool_call_id: str | None = None
    tool_name: str | None = None

class BaseLLM(ABC):
    @abstractmethod
    async def chat(
        self,
        messages: list[ChatMessage],
        tools: list[dict] | None = None,
        stream: bool = False,
    ) -> LLMResponse: ...

    @abstractmethod
    async def chat_stream(
        self,
        messages: list[ChatMessage],
        tools: list[dict] | None = None,
    ):
        """Async generator yielding LLMResponse chunks"""
        ...
```

- [ ] **Step 2: Implement OpenAI LLM with function calling**

Write `backend/llm/openai_llm.py`:
```python
import json
from openai import AsyncOpenAI
from openai.types.chat import ChatCompletionMessageParam
from config import settings
from .base import BaseLLM, LLMResponse, ToolCall, ChatMessage


class OpenAILLM(BaseLLM):
    def __init__(self):
        self.client = AsyncOpenAI(
            api_key=settings.llm_api_key,
            base_url=settings.llm_base_url,
        )
        self.model = settings.llm_model

    def _build_messages(self, messages: list[ChatMessage]) -> list[ChatCompletionMessageParam]:
        result = []
        for m in messages:
            msg: ChatCompletionMessageParam = {"role": m.role}
            if m.content is not None:
                msg["content"] = m.content
            if m.tool_call_id:
                msg["tool_call_id"] = m.tool_call_id
            if m.tool_name:
                msg["name"] = m.tool_name
            result.append(msg)
        return result

    async def chat(
        self,
        messages: list[ChatMessage],
        tools: list[dict] | None = None,
        stream: bool = False,
    ) -> LLMResponse:
        kwargs = {
            "model": self.model,
            "messages": self._build_messages(messages),
        }
        if tools:
            kwargs["tools"] = tools
            kwargs["tool_choice"] = "auto"

        response = await self.client.chat.completions.create(**kwargs)
        choice = response.choices[0]

        if choice.message.tool_calls:
            return LLMResponse(
                tool_calls=[
                    ToolCall(
                        name=tc.function.name,
                        arguments=json.loads(tc.function.arguments),
                    )
                    for tc in choice.message.tool_calls
                ],
                content=choice.message.content,
                is_final=False,
            )
        return LLMResponse(content=choice.message.content, is_final=True)

    async def chat_stream(self, messages: list[ChatMessage], tools: list[dict] | None = None):
        kwargs = {
            "model": self.model,
            "messages": self._build_messages(messages),
            "stream": True,
        }
        if tools:
            kwargs["tools"] = tools
            kwargs["tool_choice"] = "auto"

        stream = await self.client.chat.completions.create(**kwargs)
        async for chunk in stream:
            delta = chunk.choices[0].delta if chunk.choices else None
            if delta and delta.content:
                yield LLMResponse(content=delta.content, is_final=False)
        yield LLMResponse(content="", is_final=True)
```

- [ ] **Step 3: Create factory**

Write `backend/llm/factory.py`:
```python
from config import settings
from .base import BaseLLM
from .openai_llm import OpenAILLM

def create_llm() -> BaseLLM:
    if settings.llm_provider == "openai":
        return OpenAILLM()
    raise ValueError(f"Unsupported LLM provider: {settings.llm_provider}")
```

- [ ] **Step 4: Write test**

Create `backend/tests/llm/test_openai_llm.py`:
```python
import pytest
from llm.openai_llm import OpenAILLM
from llm.base import ChatMessage

@pytest.mark.asyncio
async def test_simple_chat():
    llm = OpenAILLM()
    response = await llm.chat([
        ChatMessage(role="user", content="Hello, say 'test passed' in one word.")
    ])
    assert response.content is not None
    assert len(response.content) > 0

@pytest.mark.asyncio
async def test_tool_calling():
    llm = OpenAILLM()
    tools = [{
        "type": "function",
        "function": {
            "name": "search_docs",
            "description": "Search documents",
            "parameters": {"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]},
        }
    }]
    response = await llm.chat(
        [ChatMessage(role="user", content="帮我查一下部署流程")],
        tools=tools,
    )
    if response.tool_calls:
        assert response.tool_calls[0].name == "search_docs"
    # If no tool call, that's also fine — model might answer directly
```

- [ ] **Step 5: Run test**

Run: `cd backend && pytest tests/llm/test_openai_llm.py -v`
Expected: Pass (requires OPENAI_API_KEY).

- [ ] **Step 6: Commit**

```bash
git add backend/llm/ backend/tests/llm/
git commit -m "feat: add LLM module with OpenAI function calling support"
```

---

## Task 6: Document Loaders

**Files:**
- Create: `backend/rag/__init__.py`
- Create: `backend/rag/loaders.py`

- [ ] **Step 1: Implement document loaders**

Write `backend/rag/loaders.py`:
```python
import io
import pandas as pd
from pathlib import Path


def load_pdf(file_path: str) -> str:
    import fitz  # PyMuPDF
    doc = fitz.open(file_path)
    text_parts = []
    for page in doc:
        text_parts.append(page.get_text())
    doc.close()
    return "\n\n".join(text_parts)


def load_docx(file_path: str) -> str:
    from docx import Document
    doc = Document(file_path)
    return "\n\n".join(p.text for p in doc.paragraphs if p.text.strip())


def load_txt(file_path: str) -> str:
    return Path(file_path).read_text(encoding="utf-8")


def load_md(file_path: str) -> str:
    return Path(file_path).read_text(encoding="utf-8")


def load_csv(file_path: str) -> str:
    df = pd.read_csv(file_path)
    return df.to_markdown(index=False)


def load_xlsx(file_path: str) -> str:
    df = pd.read_excel(file_path)
    return df.to_markdown(index=False)


LOADERS = {
    ".pdf": load_pdf,
    ".docx": load_docx,
    ".txt": load_txt,
    ".md": load_md,
    ".csv": load_csv,
    ".xlsx": load_xlsx,
}


def load_document(file_path: str, file_type: str) -> str:
    loader = LOADERS.get(file_type.lower())
    if not loader:
        raise ValueError(f"Unsupported file type: {file_type}")
    return loader(file_path)
```

- [ ] **Step 2: Write test with a sample file**

Create `backend/tests/rag/test_loaders.py`:
```python
import pytest
from rag.loaders import load_txt, load_md, load_csv

def test_load_txt(tmp_path):
    f = tmp_path / "test.txt"
    f.write_text("Hello World\nLine 2", encoding="utf-8")
    text = load_txt(str(f))
    assert "Hello World" in text

def test_load_md(tmp_path):
    f = tmp_path / "test.md"
    f.write_text("# Title\nContent here", encoding="utf-8")
    text = load_md(str(f))
    assert "# Title" in text
```

- [ ] **Step 3: Run test**

Run: `cd backend && pytest tests/rag/test_loaders.py -v`
Expected: Pass.

- [ ] **Step 4: Commit**

```bash
git add backend/rag/__init__.py backend/rag/loaders.py backend/tests/rag/test_loaders.py
git commit -m "feat: add document loaders for PDF/DOCX/TXT/MD/CSV/XLSX"
```

---

## Task 7: Chunk Splitter

**Files:**
- Create: `backend/rag/splitter.py`

- [ ] **Step 1: Implement token-based splitter**

Write `backend/rag/splitter.py`:
```python
import tiktoken
from dataclasses import dataclass

@dataclass
class Chunk:
    text: str
    chunk_index: int
    page_num: int | None = None

def split_text(
    text: str,
    chunk_size: int = 512,
    chunk_overlap: int = 50,
    encoding_name: str = "cl100k_base",
) -> list[Chunk]:
    enc = tiktoken.get_encoding(encoding_name)
    tokens = enc.encode(text)

    chunks: list[Chunk] = []
    start = 0
    idx = 0

    while start < len(tokens):
        end = min(start + chunk_size, len(tokens))
        chunk_tokens = tokens[start:end]
        chunk_text = enc.decode(chunk_tokens)

        # Try to break at natural boundaries for the last sentence
        if end < len(tokens):
            last_period = chunk_text.rfind("。")
            last_newline = chunk_text.rfind("\n")
            cut = max(last_period, last_newline)
            if cut > len(chunk_text) * 0.5:
                chunk_text = chunk_text[:cut + 1]
                # Recalculate end position approximately
                end = start + len(enc.encode(chunk_text))

        chunks.append(Chunk(text=chunk_text.strip(), chunk_index=idx))
        idx += 1
        start = end - chunk_overlap if end < len(tokens) else end

    return chunks
```

- [ ] **Step 2: Write test**

Create `backend/tests/rag/test_splitter.py`:
```python
from rag.splitter import split_text

def test_split_short_text():
    text = "这是一段简短的测试文本。"
    chunks = split_text(text, chunk_size=100, chunk_overlap=10)
    assert len(chunks) == 1
    assert chunks[0].text == text
    assert chunks[0].chunk_index == 0

def test_split_long_text():
    text = "这是测试。\n" * 200
    chunks = split_text(text, chunk_size=50, chunk_overlap=10)
    assert len(chunks) > 1
    for i, chunk in enumerate(chunks):
        assert chunk.chunk_index == i
        assert len(chunk.text) > 0
```

- [ ] **Step 3: Run test**

Run: `cd backend && pytest tests/rag/test_splitter.py -v`
Expected: Pass.

- [ ] **Step 4: Commit**

```bash
git add backend/rag/splitter.py backend/tests/rag/test_splitter.py
git commit -m "feat: add token-based text splitter with overlap"
```

---

## Task 8: File Storage

**Files:**
- Create: `backend/storage/__init__.py`
- Create: `backend/storage/files.py`

- [ ] **Step 1: Implement file storage**

Write `backend/storage/files.py`:
```python
import os
import shutil
from pathlib import Path
from config import settings

UPLOAD_DIR = Path(settings.upload_dir)

def save_upload(file_content: bytes, filename: str) -> str:
    """Save uploaded file, return stored path."""
    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    file_path = UPLOAD_DIR / filename

    # Handle duplicate filenames
    if file_path.exists():
        stem, suffix = file_path.stem, file_path.suffix
        counter = 1
        while file_path.exists():
            file_path = UPLOAD_DIR / f"{stem}_{counter}{suffix}"
            counter += 1

    file_path.write_bytes(file_content)
    return str(file_path)

def delete_file(file_path: str) -> None:
    """Delete uploaded file from disk."""
    try:
        os.remove(file_path)
    except FileNotFoundError:
        pass
```

- [ ] **Step 2: Write test**

Create `backend/tests/storage/test_files.py`:
```python
from storage.files import save_upload, delete_file
from pathlib import Path

def test_save_and_delete(tmp_path, monkeypatch):
    monkeypatch.setattr("storage.files.UPLOAD_DIR", tmp_path)
    path = save_upload(b"hello world", "test.txt")
    assert Path(path).exists()
    assert Path(path).read_bytes() == b"hello world"

    delete_file(path)
    assert not Path(path).exists()
```

- [ ] **Step 3: Run test**

Run: `cd backend && pytest tests/storage/test_files.py -v`
Expected: Pass.

- [ ] **Step 4: Commit**

```bash
git add backend/storage/ backend/tests/storage/
git commit -m "feat: add file storage save/delete helpers"
```

---

## Task 9: Document Ingestion Pipeline

**Files:**
- Create: `backend/rag/pipeline.py`
- Create: `backend/worker/__init__.py`
- Create: `backend/worker/ingestion.py`
- Modify: `backend/config.py` (add worker config if needed)

- [ ] **Step 1: Implement ingestion pipeline orchestrator**

Write `backend/rag/pipeline.py`:
```python
import uuid
import hashlib
from datetime import datetime
from sqlalchemy import select
from models.database import async_session
from models.schemas import Document, DocStatus
from rag.loaders import load_document
from rag.splitter import split_text, Chunk
from embedding.factory import create_embedding
from vectordb.factory import create_vectordb
from textdb.sqlite_fts import SQLiteFTS5
from storage.files import save_upload, delete_file
from config import settings


async def ingest_document(
    filename: str,
    file_content: bytes,
    file_type: str,
) -> str:
    """Run full ingestion. Returns document_id."""
    doc_id = str(uuid.uuid4())
    file_hash = hashlib.sha256(file_content).hexdigest()
    file_size = len(file_content)

    # 1. Check for duplicates
    async with async_session() as session:
        result = await session.execute(
            select(Document).where(Document.file_hash == file_hash)
        )
        if result.scalar_one_or_none():
            raise ValueError(f"File '{filename}' already exists (hash matched)")

    # 2. Save file
    file_path = save_upload(file_content, filename)

    # 3. Create document record
    async with async_session() as session:
        doc = Document(
            id=doc_id,
            filename=filename,
            file_hash=file_hash,
            file_size=file_size,
            file_type=file_type,
            status=DocStatus.uploaded,
        )
        session.add(doc)
        await session.commit()

    # 4. Run ingestion steps
    try:
        await _process_document(doc_id, file_path, file_type)
    except Exception as e:
        async with async_session() as session:
            result = await session.execute(select(Document).where(Document.id == doc_id))
            doc = result.scalar_one()
            doc.status = DocStatus.failed
            doc.error_message = str(e)
            await session.commit()
        raise

    return doc_id


async def _process_document(doc_id: str, file_path: str, file_type: str):
    async with async_session() as session:
        result = await session.execute(select(Document).where(Document.id == doc_id))
        doc = result.scalar_one()

        # Parse
        doc.status = DocStatus.parsing
        await session.commit()
        raw_text = load_document(file_path, file_type)

        # Chunk
        doc.status = DocStatus.chunking
        await session.commit()
        chunks = split_text(raw_text, settings.chunk_size, settings.chunk_overlap)

        # Embed
        doc.status = DocStatus.embedding
        await session.commit()
        embedding = create_embedding()
        texts = [c.text for c in chunks]
        vectors = await embedding.embed(texts)

        # Index: dual write
        doc.status = DocStatus.indexing
        await session.commit()

        vectordb = await create_vectordb()
        fts = SQLiteFTS5()

        points = []
        for chunk, vector in zip(chunks, vectors):
            chunk_id = f"{doc_id}_{chunk.chunk_index}"
            points.append({
                "id": chunk_id,
                "vector": vector,
                "payload": {
                    "document_id": doc_id,
                    "chunk_index": chunk.chunk_index,
                    "text": chunk.text,
                },
            })
            await fts.insert(chunk_id, doc_id, chunk.text)

        await vectordb.upsert(points)

        # Done
        doc.status = DocStatus.ready
        doc.chunk_count = len(chunks)
        doc.embedding_model = settings.embedding_model
        doc.embedding_dim = settings.embedding_dim
        await session.commit()
```

- [ ] **Step 2: Create background worker**

Write `backend/worker/ingestion.py`:
```python
import asyncio
from rag.pipeline import ingest_document


async def ingest_document_async(filename: str, file_content: bytes, file_type: str) -> str:
    """Wrapper that can be called via BackgroundTasks."""
    return await ingest_document(filename, file_content, file_type)
```

- [ ] **Step 3: Commit**

```bash
git add backend/rag/pipeline.py backend/worker/
git commit -m "feat: add document ingestion pipeline with state machine"
```

---

## Task 10: Hybrid Retriever

**Files:**
- Create: `backend/rag/retriever.py`

- [ ] **Step 1: Implement hybrid retrieval with RRF**

Write `backend/rag/retriever.py`:
```python
from dataclasses import dataclass
from embedding.factory import create_embedding
from vectordb.factory import create_vectordb
from vectordb.base import VectorSearchResult
from textdb.sqlite_fts import SQLiteFTS5
from textdb.base import TextSearchResult
from config import settings


@dataclass
class RetrievalResult:
    chunk_id: str
    document_id: str
    text: str
    score: float
    source: str  # "semantic" | "keyword" | "hybrid"


def _rrf_fusion(
    vector_results: list[VectorSearchResult],
    text_results: list[TextSearchResult],
    k: int = 5,
    rrf_k: int = 60,
) -> list[RetrievalResult]:
    """Reciprocal Rank Fusion."""
    scores: dict[str, tuple[float, str, str, str]] = {}  # chunk_id -> (score, doc_id, text, source)

    # Semantic scores
    for rank, r in enumerate(vector_results):
        rrf_score = 1.0 / (rrf_k + rank + 1)
        if r.chunk_id in scores:
            scores[r.chunk_id] = (scores[r.chunk_id][0] + rrf_score, r.document_id, r.text, "hybrid")
        else:
            scores[r.chunk_id] = (rrf_score, r.document_id, r.text, "semantic")

    # Keyword scores
    for rank, r in enumerate(text_results):
        rrf_score = 1.0 / (rrf_k + rank + 1)
        if r.chunk_id in scores:
            scores[r.chunk_id] = (scores[r.chunk_id][0] + rrf_score, r.document_id, r.text, "hybrid")
        else:
            scores[r.chunk_id] = (rrf_score, r.document_id, r.text, "keyword")

    # Sort by fused score descending
    sorted_items = sorted(scores.items(), key=lambda x: x[1][0], reverse=True)

    return [
        RetrievalResult(
            chunk_id=chunk_id,
            document_id=info[1],
            text=info[2],
            score=info[0],
            source=info[3],
        )
        for chunk_id, info in sorted_items[:k]
    ]


async def hybrid_search(query: str, top_k: int | None = None) -> list[RetrievalResult]:
    if top_k is None:
        top_k = settings.retrieval_top_k

    embedding = create_embedding()
    vectordb = await create_vectordb()
    fts = SQLiteFTS5()

    # Parallel: semantic + keyword
    query_vector = await embedding.embed_query(query)
    vector_results = await vectordb.search(query_vector, top_k=top_k * 2)
    text_results = await fts.search(query, top_k=top_k * 2)

    # RRF fusion
    return _rrf_fusion(vector_results, text_results, k=top_k)
```

- [ ] **Step 2: Write test**

Create `backend/tests/rag/test_retriever.py`:
```python
import pytest
from rag.retriever import _rrf_fusion
from vectordb.base import VectorSearchResult
from textdb.base import TextSearchResult

def test_rrf_fusion_basic():
    vec = [VectorSearchResult(chunk_id="a", document_id="d1", text="t1", score=0.9)]
    txt = [TextSearchResult(chunk_id="b", document_id="d2", text="t2", score=5.0)]

    results = _rrf_fusion(vec, txt, k=2)
    assert len(results) == 2

def test_rrf_fusion_overlap():
    vec = [VectorSearchResult(chunk_id="a", document_id="d1", text="t1", score=0.9)]
    txt = [TextSearchResult(chunk_id="a", document_id="d1", text="t1", score=5.0)]

    results = _rrf_fusion(vec, txt, k=1)
    assert len(results) == 1
    assert results[0].source == "hybrid"
```

- [ ] **Step 3: Run test**

Run: `cd backend && pytest tests/rag/test_retriever.py -v`
Expected: Pass.

- [ ] **Step 4: Commit**

```bash
git add backend/rag/retriever.py backend/tests/rag/test_retriever.py
git commit -m "feat: add hybrid retriever with RRF fusion (semantic + BM25)"
```

---

## Task 11: Agent Loop

**Files:**
- Create: `backend/agent/__init__.py`
- Create: `backend/agent/tools.py`
- Create: `backend/agent/classifier.py`
- Create: `backend/agent/context.py`
- Create: `backend/agent/loop.py`

- [ ] **Step 1: Create tool registry**

Write `backend/agent/tools.py`:
```python
from dataclasses import dataclass
from abc import ABC, abstractmethod

@dataclass
class ToolResult:
    success: bool
    data: any = None
    error: str | None = None
    retries: int = 0

class BaseTool(ABC):
    name: str
    description: str
    parameters: dict  # JSON Schema
    max_retries: int = 3
    retry_backoff: float = 1.0

    @abstractmethod
    async def execute(self, **kwargs) -> ToolResult: ...

    def to_llm_schema(self) -> dict:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            }
        }


class SearchDocsTool(BaseTool):
    name = "search_docs"
    description = "Search the knowledge base for relevant document chunks. Use when the user asks about any topic that might be in uploaded documents."
    parameters = {
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "Search query"},
            "top_k": {"type": "integer", "default": 5},
        },
        "required": ["query"],
    }

    async def execute(self, query: str, top_k: int = 5) -> ToolResult:
        from rag.retriever import hybrid_search
        try:
            results = await hybrid_search(query, top_k=top_k)
            return ToolResult(
                success=True,
                data={
                    "count": len(results),
                    "results": [
                        {"text": r.text, "document_id": r.document_id, "score": r.score}
                        for r in results
                    ],
                },
            )
        except Exception as e:
            return ToolResult(success=False, error=str(e))


class ToolRegistry:
    def __init__(self):
        self._tools: dict[str, BaseTool] = {}

    def register(self, tool: BaseTool):
        self._tools[tool.name] = tool

    def get_schemas(self) -> list[dict]:
        return [t.to_llm_schema() for t in self._tools.values()]

    async def execute(self, name: str, **kwargs) -> ToolResult:
        tool = self._tools[name]
        for attempt in range(tool.max_retries + 1):
            try:
                result = await tool.execute(**kwargs)
                result.retries = attempt
                return result
            except Exception as e:
                if attempt == tool.max_retries:
                    return ToolResult(success=False, error=str(e), retries=attempt)
                import asyncio
                await asyncio.sleep(tool.retry_backoff * (2 ** attempt))
        return ToolResult(success=False, error="max retries exceeded")

# Global registry
registry = ToolRegistry()
registry.register(SearchDocsTool())
```

- [ ] **Step 2: Create intent classifier (soft hint)**

Write `backend/agent/classifier.py`:
```python
from dataclasses import dataclass

@dataclass
class IntentHint:
    intent: str
    confidence: float
    suggested_tools: list[str]
    hint_text: str

# Simple keyword-based classifier for MVP (avoids extra LLM call)
# Upgraded in Phase 2 to LLM-based if needed
def classify_intent(query: str) -> IntentHint:
    keywords = {
        "查": "knowledge_retrieval",
        "搜索": "knowledge_retrieval",
        "找": "knowledge_retrieval",
        "什么是": "knowledge_retrieval",
        "怎么": "knowledge_retrieval",
        "如何": "knowledge_retrieval",
        "什么": "knowledge_retrieval",
        "文档": "knowledge_retrieval",
        "上传": "document_manage",
        "删除": "document_manage",
        "文件": "knowledge_retrieval",
    }

    for kw, intent in keywords.items():
        if kw in query:
            return IntentHint(
                intent=intent,
                confidence=0.6,
                suggested_tools=["search_docs"],
                hint_text=f"用户可能在询问文档相关内容，建议使用 search_docs 检索知识库",
            )

    return IntentHint(
        intent="general_chat",
        confidence=0.3,
        suggested_tools=[],
        hint_text="",
    )
```

- [ ] **Step 3: Create context manager**

Write `backend/agent/context.py`:
```python
from llm.base import ChatMessage
from config import settings

class ContextManager:
    def __init__(self, max_tokens: int = 128000):
        self.max_tokens = max_tokens

    def build_system_prompt(self, intent_hint: str, tools_description: str) -> str:
        hint_section = f"\n[路由建议] {intent_hint}" if intent_hint else ""
        return f"""你是一个知识库助手，帮助用户检索和理解已上传的文档内容。

## 工具使用规则
{tools_description}

## 回答规范
- 基于检索到的文档内容回答，不要编造信息
- 如果文档中没有相关信息，诚实告知用户
- 回答时标注信息来源（文档名、段落）
- 如果没有足够信息回答问题，可以向用户追问
{hint_section}
"""

    def trim_messages(self, messages: list[ChatMessage]) -> list[ChatMessage]:
        """Keep system prompt + last N messages within token budget.
        Simple character-based estimation for MVP.
        """
        if len(messages) <= 20:
            return messages
        # Keep system message + last 20 messages
        system = [m for m in messages if m.role == "system"]
        others = [m for m in messages if m.role != "system"]
        return system + others[-20:]
```

- [ ] **Step 4: Implement ReAct Agent Loop**

Write `backend/agent/loop.py`:
```python
import json
import time
import asyncio
from llm.base import BaseLLM, ChatMessage, LLMResponse
from llm.factory import create_llm
from agent.tools import registry, ToolResult
from agent.classifier import classify_intent
from agent.context import ContextManager
from config import settings


async def run_agent_loop(
    user_message: str,
    conversation_history: list[ChatMessage],
):
    """Async generator yielding SSE events as dicts."""
    llm = create_llm()
    ctx_manager = ContextManager()
    start_time = time.time()
    iteration = 0

    # 1. Intent classification (soft hint)
    hint = classify_intent(user_message)
    yield {"event": "status", "data": {"message": "正在分析问题..."}}

    # 2. Build messages
    tools = registry.get_schemas()
    tools_desc = "\n".join(
        f"- {t['function']['name']}: {t['function']['description']}"
        for t in tools
    )
    system_msg = ChatMessage(
        role="system",
        content=ctx_manager.build_system_prompt(hint.hint_text, tools_desc),
    )

    messages = [system_msg] + list(conversation_history)
    messages.append(ChatMessage(role="user", content=user_message))

    # 3. ReAct Loop
    while iteration < settings.max_loop_iterations:
        if time.time() - start_time > settings.max_total_time:
            yield {"event": "error", "data": {"code": "TIME_LIMIT", "message": "请求超时"}}
            break

        trimmed = ctx_manager.trim_messages(messages)
        yield {"event": "status", "data": {"message": "思考中..."}}

        response = await llm.chat(trimmed, tools=tools)

        if response.is_final:
            # Stream the final answer
            stream = llm.chat_stream(trimmed)
            async for chunk in stream:
                if chunk.content and chunk.is_final is False:
                    yield {"event": "answer_chunk", "data": {"delta": chunk.content}}
            # Extract sources from the last tool result in messages
            sources = _extract_sources(messages)
            if sources:
                yield {"event": "sources", "data": sources}
            yield {"event": "done", "data": {}}
            return

        if response.tool_calls:
            for tc in response.tool_calls:
                yield {
                    "event": "tool_call",
                    "data": {"tool": tc.name, "args": tc.arguments},
                }

                try:
                    result = await registry.execute(tc.name, **tc.arguments)
                except Exception as e:
                    result = ToolResult(success=False, error=str(e))

                yield {
                    "event": "tool_result",
                    "data": {
                        "tool": tc.name,
                        "success": result.success,
                        "result_count": len(result.data.get("results", [])) if result.data else 0,
                        "error": result.error,
                    },
                }

                # Append tool result to messages
                result_text = json.dumps(result.data, ensure_ascii=False) if result.success else f"Error: {result.error}"
                messages.append(ChatMessage(
                    role="assistant",
                    content=response.content,
                ))
                messages.append(ChatMessage(
                    role="tool",
                    content=result_text,
                    tool_name=tc.name,
                ))

        elif response.content:
            # Model is asking a clarification
            yield {"event": "clarification", "data": {"question": response.content}}
            yield {"event": "done", "data": {}}
            return

        iteration += 1

    # Loop limit reached
    yield {"event": "error", "data": {"code": "LOOP_LIMIT", "message": "思考轮次已达上限"}}
    yield {"event": "done", "data": {}}


def _extract_sources(messages: list[ChatMessage]) -> list[dict]:
    """Extract sources from the last successful tool result."""
    for msg in reversed(messages):
        if msg.role == "tool" and msg.content:
            try:
                data = json.loads(msg.content)
                if "results" in data:
                    return [
                        {"document_id": r.get("document_id", ""), "text": r.get("text", "")[:200]}
                        for r in data["results"][:5]
                    ]
            except json.JSONDecodeError:
                pass
    return []
```

- [ ] **Step 5: Commit**

```bash
git add backend/agent/
git commit -m "feat: add ReAct Agent loop with tool registry, intent classifier, context manager"
```

---

## Task 12: API Endpoints — Documents

**Files:**
- Create: `backend/api/__init__.py`
- Create: `backend/api/documents.py`
- Modify: `backend/main.py`

- [ ] **Step 1: Create document API endpoints**

Write `backend/api/documents.py`:
```python
import uuid
from fastapi import APIRouter, UploadFile, File, HTTPException, Depends
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func
from models.database import get_db
from models.schemas import Document, DocStatus
from rag.pipeline import ingest_document

router = APIRouter(prefix="/api/documents", tags=["documents"])

ALLOWED_TYPES = {
    ".pdf": "application/pdf",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".txt": "text/plain",
    ".md": "text/markdown",
    ".csv": "text/csv",
    ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
}

MAX_FILE_SIZE = 50 * 1024 * 1024  # 50MB

@router.post("/upload")
async def upload_document(
    file: UploadFile = File(...),
    db: AsyncSession = Depends(get_db),
):
    # Validate extension
    import os
    ext = os.path.splitext(file.filename or "")[1].lower()
    if ext not in ALLOWED_TYPES:
        raise HTTPException(400, f"Unsupported file type: {ext}")

    # Validate size (read content)
    content = await file.read()
    if len(content) > MAX_FILE_SIZE:
        raise HTTPException(400, f"File too large: {len(content)} bytes (max {MAX_FILE_SIZE})")

    try:
        doc_id = await ingest_document(
            filename=file.filename or "unknown",
            file_content=content,
            file_type=ext,
        )
    except ValueError as e:
        raise HTTPException(409, str(e))
    except Exception as e:
        raise HTTPException(500, f"Ingestion failed: {e}")

    # Return document info
    result = await db.execute(select(Document).where(Document.id == doc_id))
    doc = result.scalar_one()
    return {
        "id": doc.id,
        "filename": doc.filename,
        "file_size": doc.file_size,
        "file_type": doc.file_type,
        "status": doc.status.value,
        "chunk_count": doc.chunk_count,
        "created_at": doc.created_at.isoformat(),
    }


@router.get("")
async def list_documents(db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        select(Document).order_by(Document.created_at.desc())
    )
    docs = result.scalars().all()
    return [
        {
            "id": d.id,
            "filename": d.filename,
            "file_size": d.file_size,
            "file_type": d.file_type,
            "status": d.status.value,
            "chunk_count": d.chunk_count,
            "error_message": d.error_message,
            "created_at": d.created_at.isoformat(),
        }
        for d in docs
    ]


@router.delete("/{doc_id}")
async def delete_document(doc_id: str, db: AsyncSession = Depends(get_db)):
    from vectordb.factory import create_vectordb
    from textdb.sqlite_fts import SQLiteFTS5
    from storage.files import delete_file
    import os

    result = await db.execute(select(Document).where(Document.id == doc_id))
    doc = result.scalar_one_or_none()
    if not doc:
        raise HTTPException(404, "Document not found")

    # Clean up in order
    vectordb = await create_vectordb()
    fts = SQLiteFTS5()

    await vectordb.delete_by_document(doc_id)
    await fts.delete_by_document(doc_id)

    # Try to delete file
    upload_dir = "data/uploads"
    for f in os.listdir(upload_dir):
        if f.startswith(doc.filename.split(".")[0]):
            delete_file(os.path.join(upload_dir, f))

    await db.delete(doc)
    await db.commit()
    return {"status": "deleted", "id": doc_id}


@router.get("/{doc_id}/chunks")
async def get_document_chunks(doc_id: str, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Document).where(Document.id == doc_id))
    doc = result.scalar_one_or_none()
    if not doc:
        raise HTTPException(404, "Document not found")

    from textdb.sqlite_fts import SQLiteFTS5
    from sqlalchemy import text as sa_text
    from models.database import engine

    async with engine.connect() as conn:
        result = await conn.execute(
            sa_text("SELECT chunk_id, content FROM chunks_fts WHERE document_id = :did ORDER BY chunk_id"),
            {"did": doc_id},
        )
        rows = result.fetchall()

    return {
        "document_id": doc_id,
        "filename": doc.filename,
        "chunks": [
            {"chunk_id": r[0], "text": r[1]}
            for r in rows
        ],
    }


@router.post("/{doc_id}/reprocess")
async def reprocess_document(doc_id: str, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Document).where(Document.id == doc_id))
    doc = result.scalar_one_or_none()
    if not doc:
        raise HTTPException(404, "Document not found")
    if doc.status != DocStatus.failed:
        raise HTTPException(400, "Only failed documents can be reprocessed")

    from rag.pipeline import _process_document
    import os

    # Find file path
    upload_dir = "data/uploads"
    file_path = None
    for f in os.listdir(upload_dir):
        if f.startswith(doc.filename.split(".")[0]):
            file_path = os.path.join(upload_dir, f)
            break

    if not file_path:
        raise HTTPException(404, "Original file not found")

    # Reset status and retry
    doc.status = DocStatus.uploaded
    doc.error_message = None
    await db.commit()

    try:
        await _process_document(doc_id, file_path, doc.file_type)
    except Exception as e:
        raise HTTPException(500, f"Reprocessing failed: {e}")

    return {"status": "reprocessed", "id": doc_id}
```

- [ ] **Step 2: Register in main.py**

Modify `backend/main.py`, add after the health endpoint:
```python
from api.documents import router as documents_router
app.include_router(documents_router)
```

- [ ] **Step 3: Test with curl**

Start server: `cd backend && uvicorn main:app --reload`

Run: `curl -X POST http://localhost:8000/api/documents/upload -F "file=@test.txt"`
Expected: Returns document JSON with status "uploaded" or "ready".

Run: `curl http://localhost:8000/api/documents`
Expected: Returns array of documents.

- [ ] **Step 4: Commit**

```bash
git add backend/api/documents.py backend/main.py
git commit -m "feat: add document upload/list/delete/chunks/reprocess API endpoints"
```

---

## Task 13: API Endpoints — Chat (SSE)

**Files:**
- Create: `backend/api/chat.py`
- Modify: `backend/main.py`

- [ ] **Step 1: Create chat SSE endpoint**

Write `backend/api/chat.py`:
```python
import json
import uuid
from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from pydantic import BaseModel
from models.database import get_db
from models.schemas import Conversation, Message
from llm.base import ChatMessage
from agent.loop import run_agent_loop

router = APIRouter(tags=["chat"])


class ChatRequest(BaseModel):
    conversation_id: str | None = None
    message: str


async def sse_generator(user_message: str, history: list[ChatMessage]):
    async for event in run_agent_loop(user_message, history):
        event_type = event["event"]
        data = json.dumps(event["data"], ensure_ascii=False)
        yield f"event: {event_type}\ndata: {data}\n\n"


@router.post("/api/chat")
async def chat(req: ChatRequest, db: AsyncSession = Depends(get_db)):
    # Load or create conversation
    if req.conversation_id:
        result = await db.execute(
            select(Conversation).where(Conversation.id == req.conversation_id)
        )
        conv = result.scalar_one_or_none()
        if not conv:
            conv = Conversation(id=str(uuid.uuid4()), title=req.message[:50])
            db.add(conv)
            await db.commit()
        conv_id = conv.id
    else:
        conv = Conversation(id=str(uuid.uuid4()), title=req.message[:50])
        db.add(conv)
        await db.commit()
        conv_id = conv.id

    # Load history
    result = await db.execute(
        select(Message)
        .where(Message.conversation_id == conv_id)
        .order_by(Message.created_at.asc())
    )
    messages = result.scalars().all()

    history = []
    for m in messages:
        if m.role == "tool":
            history.append(ChatMessage(role=m.role, content=m.content, tool_name=m.tool_name))
        else:
            history.append(ChatMessage(role=m.role, content=m.content))

    # Save user message
    user_msg = Message(
        id=str(uuid.uuid4()),
        conversation_id=conv_id,
        role="user",
        content=req.message,
    )
    db.add(user_msg)
    await db.commit()

    return StreamingResponse(
        sse_generator(req.message, history),
        media_type="text/event-stream",
        headers={
            "X-Conversation-Id": conv_id,
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
        },
    )
```

- [ ] **Step 2: Register in main.py**

Add to `backend/main.py`:
```python
from api.chat import router as chat_router
app.include_router(chat_router)
```

- [ ] **Step 3: Commit**

```bash
git add backend/api/chat.py backend/main.py
git commit -m "feat: add SSE chat endpoint with Agent loop integration"
```

---

## Task 14: API Endpoints — Conversations

**Files:**
- Create: `backend/api/conversations.py`
- Modify: `backend/main.py`

- [ ] **Step 1: Create conversations API**

Write `backend/api/conversations.py`:
```python
import uuid
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from pydantic import BaseModel
from models.database import get_db
from models.schemas import Conversation, Message

router = APIRouter(prefix="/api/conversations", tags=["conversations"])

class CreateConversationRequest(BaseModel):
    title: str = "New Chat"

@router.get("")
async def list_conversations(db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        select(Conversation).order_by(Conversation.updated_at.desc())
    )
    convs = result.scalars().all()
    return [
        {"id": c.id, "title": c.title, "created_at": c.created_at.isoformat(), "updated_at": c.updated_at.isoformat()}
        for c in convs
    ]

@router.post("")
async def create_conversation(req: CreateConversationRequest, db: AsyncSession = Depends(get_db)):
    conv = Conversation(id=str(uuid.uuid4()), title=req.title)
    db.add(conv)
    await db.commit()
    return {"id": conv.id, "title": conv.title}

@router.delete("/{conv_id}")
async def delete_conversation(conv_id: str, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Conversation).where(Conversation.id == conv_id))
    conv = result.scalar_one_or_none()
    if not conv:
        raise HTTPException(404, "Conversation not found")
    # Also delete messages
    await db.execute(
        __import__("sqlalchemy").sql.delete(Message).where(Message.conversation_id == conv_id)
    )
    await db.delete(conv)
    await db.commit()
    return {"status": "deleted", "id": conv_id}

@router.get("/{conv_id}/messages")
async def get_messages(conv_id: str, db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        select(Message)
        .where(Message.conversation_id == conv_id)
        .order_by(Message.created_at.asc())
    )
    msgs = result.scalars().all()
    return [
        {"id": m.id, "role": m.role, "content": m.content, "tool_name": m.tool_name,
         "sources": m.sources, "created_at": m.created_at.isoformat()}
        for m in msgs
    ]
```

- [ ] **Step 2: Register in main.py**

```python
from api.conversations import router as conversations_router
app.include_router(conversations_router)
```

- [ ] **Step 3: Commit**

```bash
git add backend/api/conversations.py backend/main.py
git commit -m "feat: add conversation CRUD API endpoints"
```

---

## Task 15: Frontend Scaffold

**Files:** All frontend scaffold files

- [ ] **Step 1: Initialize Vite project**

```bash
cd D:/Python/subject1/RAG_Agent
npm create vite@latest frontend -- --template react-ts
cd frontend
npm install
npm install tailwindcss @tailwindcss/vite postcss react-router-dom zustand
npx tailwindcss init -p
```

- [ ] **Step 2: Configure Tailwind**

Write `frontend/tailwind.config.ts`:
```typescript
export default {
  content: ["./index.html", "./src/**/*.{js,ts,jsx,tsx}"],
  darkMode: "class",
  theme: { extend: {} },
  plugins: [],
};
```

Write `frontend/src/index.css`:
```css
@import "tailwindcss";

:root {
  color-scheme: dark;
}

body {
  margin: 0;
  background: #0f0f1a;
  color: #e0e0e0;
  font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
}

#root {
  height: 100vh;
}
```

- [ ] **Step 3: Create types**

Write `frontend/src/types/index.ts`:
```typescript
export interface Document {
  id: string;
  filename: string;
  file_size: number;
  file_type: string;
  status: "uploaded" | "parsing" | "chunking" | "embedding" | "indexing" | "ready" | "failed";
  chunk_count: number;
  error_message?: string;
  created_at: string;
}

export interface Conversation {
  id: string;
  title: string;
  created_at: string;
  updated_at: string;
}

export interface ChatMessage {
  id: string;
  role: "user" | "assistant" | "tool";
  content: string;
  tool_name?: string;
  sources?: string;
}

export type SSEState = "idle" | "connecting" | "streaming" | "waiting_clarify" | "error";

export interface SSEEvent {
  event: string;
  data: unknown;
}
```

Write `frontend/src/types/chat.ts`:
```typescript
export interface AgentStep {
  type: "status" | "tool_call" | "tool_result" | "clarification" | "answer_chunk" | "sources" | "error" | "done";
  data: Record<string, unknown>;
  timestamp: number;
}

export interface DisplayMessage {
  id: string;
  role: "user" | "assistant" | "system";
  content: string;
  steps: AgentStep[];
  sources?: Array<{ document_id: string; text: string }>;
  isStreaming: boolean;
}
```

Write `frontend/src/types/document.ts`:
```typescript
export interface Document {
  id: string;
  filename: string;
  file_size: number;
  file_type: string;
  status: string;
  chunk_count: number;
  error_message?: string;
  created_at: string;
}

export interface Chunk {
  chunk_id: string;
  text: string;
}

export interface DocumentChunks {
  document_id: string;
  filename: string;
  chunks: Chunk[];
}
```

- [ ] **Step 4: Create API client**

Write `frontend/src/api/client.ts`:
```typescript
const BASE_URL = "http://localhost:8000";

export async function apiGet<T>(path: string): Promise<T> {
  const res = await fetch(`${BASE_URL}${path}`);
  if (!res.ok) throw new Error(`GET ${path}: ${res.status}`);
  return res.json();
}

export async function apiPost<T>(path: string, body?: unknown): Promise<T> {
  const res = await fetch(`${BASE_URL}${path}`, {
    method: "POST",
    headers: body instanceof FormData ? {} : { "Content-Type": "application/json" },
    body: body instanceof FormData ? body : JSON.stringify(body),
  });
  if (!res.ok) {
    const err = await res.text();
    throw new Error(`POST ${path}: ${res.status} - ${err}`);
  }
  return res.json();
}

export async function apiDelete(path: string): Promise<void> {
  const res = await fetch(`${BASE_URL}${path}`, { method: "DELETE" });
  if (!res.ok) throw new Error(`DELETE ${path}: ${res.status}`);
}
```

Write `frontend/src/api/documents.ts`:
```typescript
import { apiGet, apiPost, apiDelete } from "./client";
import type { Document, DocumentChunks } from "../types/document";

export function listDocuments(): Promise<Document[]> {
  return apiGet("/api/documents");
}

export function uploadDocument(file: File): Promise<Document> {
  const form = new FormData();
  form.append("file", file);
  return apiPost("/api/documents/upload", form);
}

export function deleteDocument(id: string): Promise<void> {
  return apiDelete(`/api/documents/${id}`);
}

export function getDocumentChunks(id: string): Promise<DocumentChunks> {
  return apiGet(`/api/documents/${id}/chunks`);
}
```

Write `frontend/src/api/conversations.ts`:
```typescript
import { apiGet, apiPost, apiDelete } from "./client";

export interface ConvResponse {
  id: string;
  title: string;
  created_at: string;
  updated_at: string;
}

export function listConversations(): Promise<ConvResponse[]> {
  return apiGet("/api/conversations");
}

export function createConversation(title?: string): Promise<ConvResponse> {
  return apiPost("/api/conversations", { title: title || "New Chat" });
}

export function deleteConversation(id: string): Promise<void> {
  return apiDelete(`/api/conversations/${id}`);
}

export function getMessages(convId: string): Promise<unknown[]> {
  return apiGet(`/api/conversations/${convId}/messages`);
}
```

Write `frontend/src/api/chat.ts`:
```typescript
import type { SSEEvent } from "../types";

export function sendMessage(
  message: string,
  conversationId: string | null,
  onEvent: (event: SSEEvent) => void,
  onError: (error: Error) => void,
  onDone: () => void,
): AbortController {
  const controller = new AbortController();

  fetch("http://localhost:8000/api/chat", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ message, conversation_id: conversationId }),
    signal: controller.signal,
  })
    .then(async (response) => {
      const reader = response.body?.getReader();
      if (!reader) return;

      const decoder = new TextDecoder();
      let buffer = "";

      while (true) {
        const { done, value } = await reader.read();
        if (done) break;

        buffer += decoder.decode(value, { stream: true });
        const lines = buffer.split("\n");
        buffer = lines.pop() || "";

        let eventType = "";
        for (const line of lines) {
          if (line.startsWith("event: ")) {
            eventType = line.slice(7).trim();
          } else if (line.startsWith("data: ")) {
            try {
              const data = JSON.parse(line.slice(6));
              onEvent({ event: eventType, data });
            } catch {
              // partial chunk, ignore parse errors
            }
          }
        }
      }
    })
    .catch((err) => {
      if (err.name !== "AbortError") onError(err);
    })
    .finally(onDone);

  return controller;
}
```

- [ ] **Step 5: Commit**

```bash
cd frontend && git add -A && git commit -m "feat: scaffold frontend with Vite, Tailwind, types, API layer"
```

---

## Task 16: Frontend Stores (Zustand)

**Files:**
- Create: `frontend/src/stores/chatStore.ts`
- Create: `frontend/src/stores/documentStore.ts`
- Create: `frontend/src/hooks/useSSE.ts`

- [ ] **Step 1: Create chat store**

Write `frontend/src/stores/chatStore.ts`:
```typescript
import { create } from "zustand";
import type { DisplayMessage, AgentStep, SSEState } from "../types/chat";
import { sendMessage } from "../api/chat";
import { listConversations, createConversation } from "../api/conversations";

interface ChatStore {
  messages: DisplayMessage[];
  conversations: Array<{ id: string; title: string; updated_at: string }>;
  currentConvId: string | null;
  sseState: SSEState;
  abortController: AbortController | null;

  loadConversations: () => Promise<void>;
  newConversation: () => Promise<void>;
  switchConversation: (id: string) => void;
  send: (text: string) => Promise<void>;
  stop: () => void;
}

export const useChatStore = create<ChatStore>((set, get) => ({
  messages: [],
  conversations: [],
  currentConvId: null,
  sseState: "idle",
  abortController: null,

  loadConversations: async () => {
    const convs = await listConversations();
    set({ conversations: convs });
  },

  newConversation: async () => {
    const conv = await createConversation();
    set({ conversations: [conv, ...get().conversations], currentConvId: conv.id, messages: [] });
  },

  switchConversation: (id: string) => {
    set({ currentConvId: id, messages: [] });
  },

  send: async (text: string) => {
    const msgId = crypto.randomUUID();
    const displayMsg: DisplayMessage = {
      id: msgId,
      role: "user",
      content: text,
      steps: [],
      isStreaming: false,
    };
    const assistantMsg: DisplayMessage = {
      id: crypto.randomUUID(),
      role: "assistant",
      content: "",
      steps: [],
      isStreaming: true,
    };

    set((s) => ({
      messages: [...s.messages, displayMsg, assistantMsg],
      sseState: "connecting",
    }));

    const controller = sendMessage(
      text,
      get().currentConvId,
      (event) => {
        set((s) => {
          const msgs = [...s.messages];
          const last = msgs[msgs.length - 1];
          if (last.role !== "assistant") return s;

          const step: AgentStep = {
            type: event.event as AgentStep["type"],
            data: event.data as Record<string, unknown>,
            timestamp: Date.now(),
          };

          if (event.event === "answer_chunk") {
            last.content += (event.data as { delta: string }).delta || "";
          }
          if (event.event === "sources") {
            last.sources = event.data as Array<{ document_id: string; text: string }>;
          }
          last.steps.push(step);
          msgs[msgs.length - 1] = { ...last };

          return { messages: msgs, sseState: "streaming" };
        });
      },
      (error) => {
        set({ sseState: "error" });
      },
      () => {
        set((s) => {
          const msgs = [...s.messages];
          const last = msgs[msgs.length - 1];
          if (last.role === "assistant") {
            last.isStreaming = false;
            msgs[msgs.length - 1] = { ...last };
          }
          return { messages: msgs, sseState: "idle", abortController: null };
        });
      },
    );

    set({ abortController: controller });
  },

  stop: () => {
    get().abortController?.abort();
    set({ sseState: "idle", abortController: null });
  },
}));
```

- [ ] **Step 2: Create document store**

Write `frontend/src/stores/documentStore.ts`:
```typescript
import { create } from "zustand";
import type { Document } from "../types/document";
import { listDocuments, uploadDocument, deleteDocument, getDocumentChunks } from "../api/documents";

interface DocumentStore {
  documents: Document[];
  uploading: boolean;
  uploadProgress: string;

  load: () => Promise<void>;
  upload: (file: File) => Promise<void>;
  remove: (id: string) => Promise<void>;
  getChunks: (id: string) => Promise<unknown>;
}

export const useDocumentStore = create<DocumentStore>((set, get) => ({
  documents: [],
  uploading: false,
  uploadProgress: "",

  load: async () => {
    const docs = await listDocuments();
    set({ documents: docs });
  },

  upload: async (file: File) => {
    set({ uploading: true, uploadProgress: "上传中..." });
    try {
      set({ uploadProgress: "正在解析和入库..." });
      await uploadDocument(file);
      await get().load();
    } finally {
      set({ uploading: false, uploadProgress: "" });
    }
  },

  remove: async (id: string) => {
    await deleteDocument(id);
    await get().load();
  },

  getChunks: async (id: string) => {
    return getDocumentChunks(id);
  },
}));
```

- [ ] **Step 3: Create useSSE hook**

Write `frontend/src/hooks/useSSE.ts`:
```typescript
import { useChatStore } from "../stores/chatStore";

export function useSSE() {
  const sseState = useChatStore((s) => s.sseState);
  const send = useChatStore((s) => s.send);
  const stop = useChatStore((s) => s.stop);

  return { sseState, send, stop };
}
```

- [ ] **Step 4: Commit**

```bash
git add frontend/src/stores/ frontend/src/hooks/
git commit -m "feat: add Zustand stores for chat and documents, SSE hook"
```

---

## Task 17: Frontend — Chat Components

**Files:**
- Create: `frontend/src/components/chat/ChatPanel.tsx`
- Create: `frontend/src/components/chat/MessageList.tsx`
- Create: `frontend/src/components/chat/MessageBubble.tsx`
- Create: `frontend/src/components/chat/ChatInput.tsx`
- Create: `frontend/src/components/chat/ToolCallCard.tsx`
- Create: `frontend/src/components/chat/SourceCard.tsx`
- Create: `frontend/src/components/chat/ClarifyBubble.tsx`
- Create: `frontend/src/components/layout/Sidebar.tsx`
- Create: `frontend/src/components/layout/MainLayout.tsx`
- Modify: `frontend/src/App.tsx`

- [ ] **Step 1: Create ChatInput**

Write `frontend/src/components/chat/ChatInput.tsx`:
```tsx
import { useState, useRef, KeyboardEvent } from "react";
import { useSSE } from "../../hooks/useSSE";

export function ChatInput() {
  const [text, setText] = useState("");
  const { sseState, send, stop } = useSSE();
  const inputRef = useRef<HTMLTextAreaElement>(null);

  const isDisabled = sseState === "connecting" || sseState === "streaming";

  const handleSend = () => {
    const trimmed = text.trim();
    if (!trimmed || isDisabled) return;
    send(trimmed);
    setText("");
  };

  const handleKeyDown = (e: KeyboardEvent) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      handleSend();
    }
  };

  return (
    <div className="border-t border-gray-800 p-3">
      <div className="flex gap-2 items-end max-w-4xl mx-auto">
        <textarea
          ref={inputRef}
          value={text}
          onChange={(e) => setText(e.target.value)}
          onKeyDown={handleKeyDown}
          placeholder="输入消息... (Enter 发送, Shift+Enter 换行)"
          rows={1}
          disabled={isDisabled}
          className="flex-1 bg-gray-900 border border-gray-700 rounded-lg px-4 py-2 resize-none
                     text-gray-200 placeholder-gray-500 focus:outline-none focus:border-indigo-500
                     disabled:opacity-50"
        />
        {isDisabled ? (
          <button
            onClick={stop}
            className="px-4 py-2 bg-red-700 hover:bg-red-600 rounded-lg text-sm font-medium"
          >
            停止
          </button>
        ) : (
          <button
            onClick={handleSend}
            disabled={!text.trim()}
            className="px-4 py-2 bg-indigo-600 hover:bg-indigo-500 disabled:bg-gray-700
                       rounded-lg text-sm font-medium disabled:opacity-50"
          >
            发送
          </button>
        )}
      </div>
    </div>
  );
}
```

- [ ] **Step 2: Create MessageBubble**

Write `frontend/src/components/chat/MessageBubble.tsx`:
```tsx
import type { DisplayMessage } from "../../types/chat";
import { ToolCallCard } from "./ToolCallCard";
import { SourceCard } from "./SourceCard";
import { ClarifyBubble } from "./ClarifyBubble";
import ReactMarkdown from "react-markdown";

interface Props {
  message: DisplayMessage;
}

export function MessageBubble({ message }: Props) {
  if (message.role === "user") {
    return (
      <div className="flex justify-end mb-4">
        <div className="max-w-[75%] bg-indigo-900/60 text-gray-100 rounded-2xl rounded-br-md px-4 py-2">
          {message.content}
        </div>
      </div>
    );
  }

  return (
    <div className="mb-4">
      {/* Agent steps */}
      {message.steps.map((step, i) => {
        if (step.type === "tool_call" || step.type === "tool_result") {
          return <ToolCallCard key={i} step={step} />;
        }
        if (step.type === "clarification") {
          return <ClarifyBubble key={i} question={(step.data as { question: string }).question} />;
        }
        return null;
      })}

      {/* Answer content */}
      {message.content && (
        <div className="max-w-[85%] bg-gray-900/80 text-gray-200 rounded-2xl rounded-bl-md px-4 py-2 prose prose-invert prose-sm">
          <ReactMarkdown>{message.content}</ReactMarkdown>
          {message.isStreaming && <span className="inline-block w-2 h-4 bg-indigo-400 animate-pulse ml-1" />}
        </div>
      )}

      {/* Sources */}
      {message.sources && message.sources.length > 0 && (
        <div className="mt-2">
          <SourceCard sources={message.sources} />
        </div>
      )}
    </div>
  );
}
```

- [ ] **Step 3: Create ToolCallCard**

Write `frontend/src/components/chat/ToolCallCard.tsx`:
```tsx
import { useState } from "react";
import type { AgentStep } from "../../types/chat";

export function ToolCallCard({ step }: { step: AgentStep }) {
  const [expanded, setExpanded] = useState(false);
  const data = step.data as Record<string, unknown>;

  if (step.type === "tool_call") {
    return (
      <div
        className="text-xs text-gray-400 mb-1 cursor-pointer hover:text-gray-300"
        onClick={() => setExpanded(!expanded)}
      >
        🔧 调用工具: <span className="text-indigo-400">{data.tool as string}</span>
        {expanded && (
          <span className="ml-2 text-gray-500">
            {JSON.stringify(data.args).slice(0, 100)}
          </span>
        )}
      </div>
    );
  }

  if (step.type === "tool_result") {
    return (
      <div className="text-xs text-gray-400 mb-1">
        {data.success ? "✅" : "❌"} 结果: {(data.result_count as number) || 0} 条记录
        {data.error && <span className="text-red-400 ml-2">{data.error as string}</span>}
      </div>
    );
  }

  return null;
}
```

- [ ] **Step 4: Create SourceCard**

Write `frontend/src/components/chat/SourceCard.tsx`:
```tsx
export function SourceCard({ sources }: { sources: Array<{ document_id: string; text: string }> }) {
  return (
    <div className="flex gap-2 flex-wrap">
      <span className="text-xs text-gray-500">📎 来源:</span>
      {sources.map((s, i) => (
        <span
          key={i}
          className="text-xs bg-gray-800 text-gray-300 px-2 py-0.5 rounded cursor-pointer hover:bg-gray-700"
          title={s.text}
        >
          {s.document_id.slice(0, 8)}... — {s.text.slice(0, 60)}...
        </span>
      ))}
    </div>
  );
}
```

- [ ] **Step 5: Create ClarifyBubble**

Write `frontend/src/components/chat/ClarifyBubble.tsx`:
```tsx
export function ClarifyBubble({ question }: { question: string }) {
  return (
    <div className="max-w-[80%] bg-amber-900/40 border border-amber-700/50 rounded-lg px-3 py-2 mb-2">
      <span className="text-xs text-amber-400 mr-1">❓</span>
      <span className="text-sm text-amber-100">{question}</span>
    </div>
  );
}
```

- [ ] **Step 6: Create MessageList**

Write `frontend/src/components/chat/MessageList.tsx`:
```tsx
import { useChatStore } from "../../stores/chatStore";
import { MessageBubble } from "./MessageBubble";
import { useEffect, useRef } from "react";

export function MessageList() {
  const messages = useChatStore((s) => s.messages);
  const sseState = useChatStore((s) => s.sseState);
  const bottomRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages]);

  return (
    <div className="flex-1 overflow-y-auto p-4">
      {messages.length === 0 && (
        <div className="flex items-center justify-center h-full text-gray-500 text-sm">
          上传文档后开始提问
        </div>
      )}
      {messages.map((msg) => (
        <MessageBubble key={msg.id} message={msg} />
      ))}
      {sseState === "connecting" && (
        <div className="text-xs text-gray-500 mb-2">⏳ 连接中...</div>
      )}
      <div ref={bottomRef} />
    </div>
  );
}
```

- [ ] **Step 7: Create ChatPanel**

Write `frontend/src/components/chat/ChatPanel.tsx`:
```tsx
import { MessageList } from "./MessageList";
import { ChatInput } from "./ChatInput";

export function ChatPanel() {
  return (
    <div className="flex-1 flex flex-col h-full">
      <MessageList />
      <ChatInput />
    </div>
  );
}
```

- [ ] **Step 8: Commit**

```bash
git add frontend/src/components/chat/
git commit -m "feat: add chat components — MessageBubble, ToolCallCard, SourceCard, ChatInput, ChatPanel"
```

---

## Task 18: Frontend — Document Management Components

**Files:**
- Create: `frontend/src/components/documents/DocumentList.tsx`
- Create: `frontend/src/components/documents/DocumentCard.tsx`
- Create: `frontend/src/components/documents/UploadZone.tsx`
- Create: `frontend/src/components/documents/ChunkViewer.tsx`

- [ ] **Step 1: Create DocumentCard**

Write `frontend/src/components/documents/DocumentCard.tsx`:
```tsx
import type { Document } from "../../types/document";

interface Props {
  doc: Document;
  onDelete: (id: string) => void;
  onViewChunks: (id: string) => void;
}

export function DocumentCard({ doc, onDelete, onViewChunks }: Props) {
  const statusColors: Record<string, string> = {
    ready: "text-green-400",
    failed: "text-red-400",
    uploaded: "text-yellow-400",
    parsing: "text-yellow-400",
    chunking: "text-yellow-400",
    embedding: "text-yellow-400",
    indexing: "text-yellow-400",
  };

  const sizeMB = (doc.file_size / 1024 / 1024).toFixed(1);

  return (
    <div className="bg-gray-900/60 rounded-lg p-3 flex items-center gap-3 hover:bg-gray-900/80 transition">
      <span className="text-xl">📄</span>
      <div className="flex-1 min-w-0">
        <p className="text-sm font-medium truncate">{doc.filename}</p>
        <p className="text-xs text-gray-500">
          {sizeMB} MB · {doc.chunk_count} chunks ·
          <span className={statusColors[doc.status] || "text-gray-400"}> {doc.status}</span>
          {doc.error_message && (
            <span className="text-red-400 ml-1" title={doc.error_message}>⚠</span>
          )}
        </p>
      </div>
      <div className="flex gap-1">
        <button
          onClick={() => onViewChunks(doc.id)}
          className="text-gray-500 hover:text-gray-300 px-2 py-1 text-xs"
        >
          查看切片
        </button>
        <button
          onClick={() => onDelete(doc.id)}
          className="text-gray-500 hover:text-red-400 px-2 py-1 text-xs"
        >
          删除
        </button>
      </div>
    </div>
  );
}
```

- [ ] **Step 2: Create UploadZone**

Write `frontend/src/components/documents/UploadZone.tsx`:
```tsx
import { useCallback } from "react";
import { useDocumentStore } from "../../stores/documentStore";

export function UploadZone() {
  const { upload, uploading, uploadProgress } = useDocumentStore();

  const handleDrop = useCallback(
    (e: React.DragEvent) => {
      e.preventDefault();
      const files = Array.from(e.dataTransfer.files);
      files.forEach((f) => upload(f));
    },
    [upload],
  );

  const handleFileSelect = useCallback(
    (e: React.ChangeEvent<HTMLInputElement>) => {
      const files = Array.from(e.target.files || []);
      files.forEach((f) => upload(f));
    },
    [upload],
  );

  return (
    <div
      onDrop={handleDrop}
      onDragOver={(e) => e.preventDefault()}
      className="border-2 border-dashed border-gray-700 rounded-lg p-6 text-center
                 hover:border-indigo-500 transition cursor-pointer"
    >
      {uploading ? (
        <div className="text-sm text-gray-400">
          <div className="animate-spin inline-block w-4 h-4 border-2 border-indigo-400 border-t-transparent rounded-full mr-2" />
          {uploadProgress}
        </div>
      ) : (
        <>
          <p className="text-gray-400 text-sm">📁 拖拽文件到此处上传</p>
          <p className="text-gray-600 text-xs mt-1">支持 PDF / Word / TXT / MD / CSV / Excel · 最大 50MB</p>
          <label className="mt-2 inline-block px-3 py-1 bg-indigo-600 hover:bg-indigo-500 rounded text-xs cursor-pointer">
            选择文件
            <input type="file" className="hidden" multiple onChange={handleFileSelect} />
          </label>
        </>
      )}
    </div>
  );
}
```

- [ ] **Step 3: Create ChunkViewer modal**

Write `frontend/src/components/documents/ChunkViewer.tsx`:
```tsx
import { useState, useEffect } from "react";
import type { DocumentChunks } from "../../types/document";
import { useDocumentStore } from "../../stores/documentStore";

interface Props {
  docId: string;
  onClose: () => void;
}

export function ChunkViewer({ docId, onClose }: Props) {
  const [data, setData] = useState<DocumentChunks | null>(null);
  const { getChunks } = useDocumentStore();

  useEffect(() => {
    getChunks(docId).then(setData);
  }, [docId, getChunks]);

  return (
    <div className="fixed inset-0 bg-black/60 flex items-center justify-center z-50" onClick={onClose}>
      <div
        className="bg-gray-900 rounded-lg w-[700px] max-h-[80vh] flex flex-col"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="p-4 border-b border-gray-800 flex justify-between items-center">
          <h3 className="font-medium">{data?.filename}</h3>
          <button onClick={onClose} className="text-gray-500 hover:text-gray-300">✕</button>
        </div>
        <div className="flex-1 overflow-y-auto p-4">
          {data?.chunks.map((chunk, i) => (
            <div key={i} className="mb-3 p-3 bg-gray-800/50 rounded text-sm">
              <span className="text-xs text-gray-500 mb-1 block">Chunk {i}</span>
              <p className="text-gray-300">{chunk.text.slice(0, 500)}</p>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}
```

- [ ] **Step 4: Create DocumentList page**

Write `frontend/src/components/documents/DocumentList.tsx`:
```tsx
import { useEffect, useState } from "react";
import { useDocumentStore } from "../../stores/documentStore";
import { DocumentCard } from "./DocumentCard";
import { UploadZone } from "./UploadZone";
import { ChunkViewer } from "./ChunkViewer";

export function DocumentList() {
  const { documents, load, remove } = useDocumentStore();
  const [viewChunksId, setViewChunksId] = useState<string | null>(null);

  useEffect(() => { load(); }, [load]);

  return (
    <div className="flex-1 p-6 overflow-y-auto">
      <h2 className="text-lg font-semibold mb-4">文档管理</h2>
      <UploadZone />
      <div className="mt-4 space-y-2">
        {documents.map((doc) => (
          <DocumentCard
            key={doc.id}
            doc={doc}
            onDelete={remove}
            onViewChunks={setViewChunksId}
          />
        ))}
        {documents.length === 0 && (
          <p className="text-center text-gray-600 text-sm mt-8">暂无文档</p>
        )}
      </div>
      {viewChunksId && <ChunkViewer docId={viewChunksId} onClose={() => setViewChunksId(null)} />}
    </div>
  );
}
```

- [ ] **Step 5: Commit**

```bash
git add frontend/src/components/documents/
git commit -m "feat: add document management components — upload, list, chunk viewer"
```

---

## Task 19: Frontend — Layout & Routing

**Files:**
- Create: `frontend/src/components/layout/Sidebar.tsx`
- Create: `frontend/src/components/layout/MainLayout.tsx`
- Modify: `frontend/src/App.tsx`

- [ ] **Step 1: Create Sidebar**

Write `frontend/src/components/layout/Sidebar.tsx`:
```tsx
import { useEffect } from "react";
import { useChatStore } from "../../stores/chatStore";
import { Link, useLocation } from "react-router-dom";

export function Sidebar() {
  const { conversations, loadConversations, newConversation, currentConvId, switchConversation } =
    useChatStore();
  const location = useLocation();

  useEffect(() => { loadConversations(); }, [loadConversations]);

  return (
    <div className="w-56 bg-gray-950 border-r border-gray-800 flex flex-col h-full">
      <div className="p-3 border-b border-gray-800">
        <button
          onClick={newConversation}
          className="w-full py-2 bg-indigo-600 hover:bg-indigo-500 rounded-lg text-sm font-medium"
        >
          + 新建会话
        </button>
      </div>

      <div className="flex-1 overflow-y-auto p-2">
        {conversations.map((conv) => (
          <button
            key={conv.id}
            onClick={() => switchConversation(conv.id)}
            className={`w-full text-left px-3 py-2 rounded text-sm truncate mb-1
              ${conv.id === currentConvId ? "bg-gray-800 text-gray-100" : "text-gray-400 hover:bg-gray-900"}`}
          >
            💬 {conv.title}
          </button>
        ))}
      </div>

      <div className="p-2 border-t border-gray-800">
        <Link
          to="/documents"
          className={`block px-3 py-2 rounded text-sm ${
            location.pathname === "/documents" ? "bg-gray-800 text-gray-100" : "text-gray-400 hover:bg-gray-900"
          }`}
        >
          📄 文档管理
        </Link>
        <Link
          to="/"
          className={`block px-3 py-2 rounded text-sm ${
            location.pathname === "/" ? "bg-gray-800 text-gray-100" : "text-gray-400 hover:bg-gray-900"
          }`}
        >
          💬 聊天
        </Link>
      </div>
    </div>
  );
}
```

- [ ] **Step 2: Create MainLayout**

Write `frontend/src/components/layout/MainLayout.tsx`:
```tsx
import { Sidebar } from "./Sidebar";
import { Outlet } from "react-router-dom";

export function MainLayout() {
  return (
    <div className="flex h-full">
      <Sidebar />
      <Outlet />
    </div>
  );
}
```

- [ ] **Step 3: Create App.tsx with routing**

Write `frontend/src/App.tsx`:
```tsx
import { BrowserRouter, Routes, Route } from "react-router-dom";
import { MainLayout } from "./components/layout/MainLayout";
import { ChatPanel } from "./components/chat/ChatPanel";
import { DocumentList } from "./components/documents/DocumentList";

export default function App() {
  return (
    <BrowserRouter>
      <Routes>
        <Route element={<MainLayout />}>
          <Route path="/" element={<ChatPanel />} />
          <Route path="/documents" element={<DocumentList />} />
        </Route>
      </Routes>
    </BrowserRouter>
  );
}
```

- [ ] **Step 4: Update main.tsx**

Modify `frontend/src/main.tsx`:
```tsx
import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import "./index.css";
import App from "./App";

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <App />
  </StrictMode>,
);
```

- [ ] **Step 5: Verify frontend builds**

Run: `cd frontend && npm run build`
Expected: Build succeeds with no errors.

- [ ] **Step 6: Commit**

```bash
git add frontend/src/components/layout/ frontend/src/App.tsx frontend/src/main.tsx
git commit -m "feat: add layout, routing, sidebar, and app shell"
```

---

## Task 20: Integration & E2E Test

**Files:**
- Create: `backend/tests/test_e2e_mvp.py`

- [ ] **Step 1: Create end-to-end test**

Write `backend/tests/test_e2e_mvp.py`:
```python
import pytest
import asyncio
from httpx import AsyncClient, ASGITransport
from main import app


@pytest.mark.asyncio
async def test_upload_and_chat_flow():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # 1. Upload a document
        with open("tests/fixtures/sample.txt", "w", encoding="utf-8") as f:
            f.write("项目部署流程：\n1. 配置环境变量\n2. 安装依赖\n3. 启动服务\n")

        with open("tests/fixtures/sample.txt", "rb") as f:
            response = await client.post(
                "/api/documents/upload",
                files={"file": ("sample.txt", f, "text/plain")},
            )
        assert response.status_code == 200
        doc = response.json()
        assert doc["status"] in ("uploaded", "ready")

        # 2. List documents
        response = await client.get("/api/documents")
        assert response.status_code == 200
        docs = response.json()
        assert len(docs) >= 1

        # 3. Chat (SSE)
        response = await client.post(
            "/api/chat",
            json={"message": "部署流程是什么？"},
        )
        assert response.status_code == 200
        # SSE response should contain answer_chunk events
        body = response.text
        assert "部署" in body or "answer_chunk" in body

        # 4. Delete document
        doc_id = doc["id"]
        response = await client.delete(f"/api/documents/{doc_id}")
        assert response.status_code == 200
```

- [ ] **Step 2: Run E2E test**

Run: `cd backend && pytest tests/test_e2e_mvp.py -v`
Expected: All steps pass.

- [ ] **Step 3: Commit**

```bash
git add backend/tests/test_e2e_mvp.py backend/tests/fixtures/
git commit -m "test: add MVP end-to-end test (upload → list → chat → delete)"
```

---

## Task 21: Final Integration Verification

- [ ] **Step 1: Start Qdrant**

```bash
docker run -d -p 6333:6333 qdrant/qdrant
```

- [ ] **Step 2: Start backend**

```bash
cd backend && uvicorn main:app --reload
```

- [ ] **Step 3: Start frontend**

```bash
cd frontend && npm run dev
```

- [ ] **Step 4: Manual smoke test**

1. Open http://localhost:5173
2. Click "文档管理" → Upload a PDF or TXT file
3. Wait for status "ready"
4. Click "聊天" → Type "刚才上传的文档说了什么？"
5. Verify: Agent responds with content from the document + source citations
6. Type "你好" → Verify simple chat works without tool calls
7. Delete the document → Verify document disappears from list

- [ ] **Step 5: Commit any final fixes**

---

## Summary

**Total tasks:** 21
**MVP scope:** Document upload → ingestion pipeline → hybrid retrieval → SSE chat with source citations
**Not included:** Long memory, OCR, web search, calculator, settings center, reranker (Phase 2/3)
