# Troubleshooting

## API Key Issues

### Service starts but LLM/embedding calls fail

Check dependency status:
```
GET /api/health/dependencies
```

If `embedding` or `llm` shows `"missing_api_key"`:
- Verify `backend/.env` has `LLM_API_KEY` and `EMBEDDING_API_KEY` set
- Verify the values are not masked (should not contain `***`)

### "Connection refused" or timeout

- Check `LLM_BASE_URL` / `EMBEDDING_BASE_URL` — must start with `http://` or `https://`
- If using DeepSeek or Qwen, ensure the base URL matches their API docs
- Try the test connection endpoint: `POST /api/settings/test-connection`

## Qdrant Issues

### "Collection dimension mismatch"

The embedding model's output dimension doesn't match the existing Qdrant collection.

**Fix:** Rebuild all indexes:
```
POST /api/settings/rebuild-collections
```

This will re-chunk, re-embed, and re-index all documents. Progress is streamed via SSE.

### "No such file or directory" on Windows

Qdrant local mode stores data in `./data/qdrant2` by default. Ensure:
- The directory exists and is writable
- No spaces in the path
- Antivirus is not blocking access

## Dependency Issues

### Module not found: ddgs / duckduckgo_search

These are web search dependencies. Install them:
```bash
pip install beautifulsoup4 duckduckgo_search
```

Or set `WEB_SEARCH_ENABLED=false` in `.env` if you don't need web search.

### PaddleOCR fails to initialize

OCR is a heavy optional dependency. Install separately:
```bash
pip install -r backend/requirements-ocr.txt
```

### sentence-transformers / torch import error

Reranker is optional. Install:
```bash
pip install -r backend/requirements-rerank.txt
```

Or set `RERANK_ENABLED=false` in `.env`.

## Document Ingestion Issues

### Upload stuck on "parsing" / "chunking" / "embedding"

Documents stuck > 30 minutes are automatically marked as failed on next startup.
Check backend logs in `./data/logs/`.

### "Duplicate file" (409 error)

The file was already uploaded (SHA-256 match). Rename and re-upload if you need a duplicate.

## Frontend Issues

### Blank page / API calls failing in browser

In Docker mode, ensure `docker-compose.yml` has:
```yaml
VITE_API_BASE_URL=http://localhost:8000
```
Not `http://backend:8000` (that's the container-internal address).

### TypeScript errors on build

Run `npm install` to ensure all dependencies are up to date.
If issues persist, delete `node_modules` and reinstall:
```bash
rm -rf node_modules && npm install
```

## Encoding Issues (Windows)

### Chinese characters display as garbled text

- Set terminal encoding to UTF-8: `chcp 65001`
- Ensure `.env` file is saved as UTF-8 (not GBK)
- PowerShell: `$OutputEncoding = [Console]::OutputEncoding = [Text.UTF8Encoding]::UTF8`

## Still Stuck?

Open an issue with:
- OS and Python version (`python --version`)
- Redacted config (`cat backend/.env | sed 's/=.*/=***/'`)
- Relevant logs from `./data/logs/`
- Stack trace if applicable
