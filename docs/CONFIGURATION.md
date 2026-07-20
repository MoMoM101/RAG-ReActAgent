# Configuration

All settings are loaded from `backend/.env` via `pydantic-settings`. Copy `backend/.env.example` to `backend/.env` and fill in your values.

## LLM

| Variable | Default | Description |
|----------|---------|-------------|
| `LLM_PROVIDER` | `openai` | Provider: `openai`, `deepseek`, `zhipu`, `moonshot`, `qwen`, `custom` |
| `LLM_MODEL` | `gpt-4o` | Model name |
| `LLM_BASE_URL` | `https://api.openai.com/v1` | API endpoint |
| `LLM_API_KEY` | — | API key (stored encrypted) |
| `LLM_MAX_CONTEXT` | `0` | Max context tokens (`0` = auto-detect from model) |

## Embedding

| Variable | Default | Description |
|----------|---------|-------------|
| `EMBEDDING_PROVIDER` | `openai` | Provider (same options as LLM) |
| `EMBEDDING_MODEL` | `text-embedding-3-small` | Model name |
| `EMBEDDING_BASE_URL` | `https://api.openai.com/v1` | API endpoint |
| `EMBEDDING_API_KEY` | — | API key (falls back to `LLM_API_KEY`) |
| `EMBEDDING_DIM` | `1536` | Vector dimension (auto-detected on first use) |

## Vector Database (Qdrant)

| Variable | Default | Description |
|----------|---------|-------------|
| `QDRANT_HOST` | — | Remote host (empty = local mode) |
| `QDRANT_PORT` | `6333` | REST port |
| `QDRANT_PATH` | `./data/qdrant2` | Local data directory |
| `QDRANT_COLLECTION` | `rag_chunks` | Collection name |

## Storage

| Variable | Default | Description |
|----------|---------|-------------|
| `DATABASE_URL` | `sqlite+aiosqlite:///./data/rag_agent.db` | SQLite path |
| `UPLOAD_DIR` | `./data/uploads` | Uploaded files directory |
| `DOCUMENT_MAX_UPLOAD_MB` | `200` | Maximum document upload size; valid range `1-512` MB |
| `DOCUMENT_BATCH_MAX_FILES` | `50` | Maximum files accepted in one batch; valid range `2-200` |
| `DOCUMENT_BATCH_MAX_TOTAL_MB` | `1024` | Maximum total size of one batch; valid range `1-10240` MB |

## Agent

| Variable | Default | Description |
|----------|---------|-------------|
| `MAX_LOOP_ITERATIONS` | `10` | Max ReAct loop iterations |
| `MAX_TOOL_RETRIES` | `3` | Max retries per tool call |
| `MAX_TOTAL_TIME` | `120` | Max total seconds per request |

## Chunking & Retrieval

| Variable | Default | Description |
|----------|---------|-------------|
| `CHUNK_SIZE` | `200` | Tokens per chunk |
| `CHUNK_OVERLAP` | `40` | Token overlap between chunks |
| `RETRIEVAL_TOP_K` | `8` | Results returned to LLM |

## Reranker (optional)

Requires `pip install -r backend/requirements-rerank.txt`.

| Variable | Default | Description |
|----------|---------|-------------|
| `RERANK_ENABLED` | `false` | Enable cross-encoder rerank |
| `RERANK_MODEL` | `BAAI/bge-reranker-v2-m3` | Model name |
| `RRF_K` | `60` | RRF smoothing factor |
| `RRF_SEMANTIC_WEIGHT` | `2.0` | Semantic weight in RRF |
| `RRF_KEYWORD_WEIGHT` | `1.0` | Keyword weight in RRF |

## Web Search (optional)

Bing primary, DuckDuckGo fallback. Requires `beautifulsoup4` and `duckduckgo_search` (included in base `requirements.txt`).

| Variable | Default | Description |
|----------|---------|-------------|
| `WEB_SEARCH_ENABLED` | `false` | Enable web search tool |
| `WEB_SEARCH_MAX_RESULTS` | `5` | Results per query |
| `WEB_SEARCH_PROXY` | — | HTTP proxy for DDG |

## OCR (optional)

Requires `pip install -r backend/requirements-ocr.txt`.

| Variable | Default | Description |
|----------|---------|-------------|
| `OCR_ENABLED` | `true` | Enable OCR for images |
| `OCR_MIN_TEXT_LENGTH` | `50` | Skip images with less text |

## Memory

| Variable | Default | Description |
|----------|---------|-------------|
| `MEMORY_ENABLED` | `true` | Enable user profile extraction |
| `MEMORY_MAX_COUNT` | `100` | Max memories stored |

## Ingestion

| Variable | Default | Description |
|----------|---------|-------------|
| `INGESTION_MAX_CONCURRENCY` | `3` | Max concurrent background ingestion |

## Logging

| Variable | Default | Description |
|----------|---------|-------------|
| `LOG_LEVEL` | `INFO` | Logging level |

## Server

| Variable | Default | Description |
|----------|---------|-------------|
| `SECRET_KEY` | auto-generated | AES-256 key for API key encryption |

## Health Check

```
GET /api/health              → {"status": "ok"}
GET /api/health/dependencies  → {"status": "degraded", "dependencies": {...}}
GET /api/health/tasks         → {"running": [...], "history": [...]}
```
