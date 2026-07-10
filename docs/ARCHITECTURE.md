# Architecture

## Module Map

```
main.py                 ← unified launcher (backend + frontend)
backend/
├── main.py             ← FastAPI app, lifespan, /api/health
├── config.py           ← pydantic-settings (reads .env)
├── api/                ← REST endpoints
│   ├── chat.py         ← POST /api/chat (SSE streaming)
│   ├── documents.py    ← document CRUD, upload, progress
│   ├── conversations.py
│   ├── settings.py     ← config, connection test, rebuild
│   ├── memories.py
│   └── backup.py       ← tar.gz backup/restore
├── agent/              ← ReAct Agent
│   ├── loop.py         ← main agent loop (async generator, SSE)
│   ├── tools.py        ← tool registry (6 tools)
│   ├── classifier.py   ← intent classification (rules + LLM)
│   ├── intercept.py    ← memory pre-intercept (regex + LLM)
│   ├── context.py      ← system prompt + message trimming
│   └── prompts/system.txt
├── rag/                ← RAG pipeline
│   ├── pipeline.py     ← document ingestion
│   ├── retriever.py    ← hybrid search + RRF fusion
│   ├── splitter.py     ← tiktoken chunker
│   └── loaders.py      ← PDF, DOCX, CSV, XLSX, TXT, MD, images
├── textdb/             ← full-text search
│   └── bm25_search.py  ← BM25 with jieba + SQLite
├── vectordb/           ← vector database
│   └── qdrant.py       ← Qdrant client (local/remote)
├── embedding/          ← embedding abstraction
├── llm/                ← LLM abstraction (OpenAI-compatible)
├── reranker/           ← cross-encoder reranker
├── ocr/                ← PaddleOCR
├── memory/             ← user profile + memory
├── models/             ← SQLAlchemy ORM + migrations
└── worker/             ← background task manager
frontend/
├── src/
│   ├── api/            ← API client modules
│   ├── components/     ← React components
│   ├── stores/         ← Zustand stores
│   └── hooks/          ← custom hooks (useSSE)
└── e2e/                ← Playwright E2E
```

## RAG Retrieval Flow

```
User Query
    │
    ├─ (optional) Query Rewriter → generates keyword variants
    │
    ├─ Parallel Search ───────────┐
    │   ├─ Qdrant (semantic)      │
    │   └─ BM25 (keyword)         │
    │                             │
    └─ RRF Fusion ←───────────────┘
        │                         
        ├─ Content Deduplication (SequenceMatcher)
        │
        ├─ Quality Filtering (regex + optional LLM)
        │
        ├─ (optional) Cross-Encoder Rerank
        │
        └─ Top-K Results
```

## Agent ReAct Loop

```
User Message
    │
    ├─ Intent Classifier (regex rules → LLM fallback)
    │
    ├─ Memory Intercept (regex → LLM batch confirm)
    │
    ├─ Context Manager (system prompt + trim history)
    │
    └─ ReAct Loop (max 10 iterations, 120s timeout)
        │
        ├─ LLM Stream → SSE (thoughts, content, tool calls)
        │
        ├─ Tool Execution (with retry)
        │   ├─ search_docs     → hybrid search
        │   ├─ web_search      → Bing → DDG fallback
        │   ├─ calculator      → AST-safe eval
        │   ├─ list_documents
        │   ├─ get_document_info
        │   └─ recall_memory
        │
        ├─ Tool Result → LLM context
        │
        └─ Final Answer → SSE done
```

## Document Ingestion Flow

```
File Upload
    │
    ├─ SHA-256 duplicate check
    │
    ├─ Parse (PDF with OCR / DOCX / CSV / XLSX / TXT / MD)
    │
    ├─ Chunk (tiktoken, paragraph-first, 200 tokens, 40 overlap)
    │
    ├─ Embed (OpenAI-compatible API, batch size 10)
    │
    └─ Dual Index
        ├─ Qdrant (vector)
        └─ BM25 (full-text with jieba)
```

## Key Design Decisions

- **No LangChain**: Custom ReAct loop for full control
- **Singleton pattern**: LLM, Embedding, Qdrant use singletons (test-resettable)
- **Double-buffer collection switching**: Qdrant rebuild creates new collection then atomically switches pointer
- **Graceful degradation**: OCR cascade, embedding auto-shrink, web search dual fallback
- **API key encryption**: AES-256 at rest in .env
