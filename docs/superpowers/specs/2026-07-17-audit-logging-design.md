# Audit Logging & Log Sanitization — Design (Phase 5b)

> Date: 2026-07-17
> Phase: 5b (per `NEXT_PHASE_OPTIMIZATION_EXECUTION_PLAN_2026-07-17.md`)
> Status: approved, ready for implementation planning

## 1. Goal

Record every high-risk operation with actor identity, timestamp, and result. Sanitize access logs so no credentials or source text leak. Provide a query API for system_admins to review the audit trail.

## 2. Scope

### In
- `audit_logs` table + migration
- `audit.py` helper: `record_audit(action, object_type, object_id, result, detail, request)`
- Wire audit calls at 9 high-risk endpoints (login, upload, delete, clear-all, backup, restore, user create/update/delete)
- Log sanitization: mask Authorization headers, query-string tokens, skip request body logging
- `GET /api/audit/` query API (system_admin only, supports `?action=&limit=&offset=`)

### Out
- Frontend audit log viewer (kept for later)
- Docker non-root user, SBOM, vulnerability scanning (separate infra task)
- Nginx security headers (already have X-Content-Type-Options from Phase 0)
- Rate limiting changes (already in place)

## 3. Data Model

```sql
CREATE TABLE audit_logs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    actor_id TEXT NOT NULL,
    actor_username TEXT NOT NULL,
    action TEXT NOT NULL,
    object_type TEXT DEFAULT '',
    object_id TEXT DEFAULT '',
    result TEXT NOT NULL DEFAULT 'success',
    detail TEXT DEFAULT '',
    request_id TEXT DEFAULT '',
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
```

No ORM model — use raw SQL via `async_session` for minimal overhead (audit is write-only from the app's perspective, query-only from the API).

## 4. Audit Helper (`backend/audit.py`)

```python
async def record_audit(
    action: str,
    object_type: str = "",
    object_id: str = "",
    result: str = "success",
    detail: str = "",
    request: Request | None = None,
) -> None:
```

Extracts `actor_id`, `actor_username` from `request.state.user` (set by JWT middleware), `request_id` from `request.state.request_id`. Inserts into `audit_logs`. Best-effort — catches and logs exceptions but never re-raises.

## 5. Wiring (one line per endpoint)

| Endpoint | Action | object_type |
|---|---|---|
| `POST /api/auth/login` (success) | `login_success` | — |
| `POST /api/auth/login` (failure) | `login_failure` | — |
| `POST /api/documents/upload` | `document_upload` | document |
| `POST /api/documents/upload-batch` | `document_upload_batch` | document |
| `DELETE /api/documents/{id}` | `document_delete` | document |
| `DELETE /api/documents/clear-all` | `document_clear_all` | document |
| `GET /api/backup` | `backup_download` | backup |
| `POST /api/backup/restore` | `backup_restore` | backup |
| `POST /api/users/` | `user_create` | user |
| `PATCH /api/users/{id}` | `user_update` | user |
| `DELETE /api/users/{id}` | `user_delete` | user |

## 6. Log Sanitization

In `middleware/logging.py`:
- Skip `Authorization` header from request logging
- Mask query parameter values for `token`, `secret`, `key`, `api_key`
- Never log request body

## 7. Query API

`GET /api/audit/` — system_admin only:
- `?action=document_delete` — filter by action
- `?limit=50&offset=0` — pagination
- Returns `[{id, actor_username, action, object_type, object_id, result, detail, request_id, created_at}]`

## 8. Files

| File | Change |
|---|---|
| `backend/models/database.py` | audit_logs table migration |
| `backend/audit.py` | New: record_audit + query helpers |
| `backend/api/audit.py` | New: GET /api/audit/ |
| `backend/api/auth.py` | +2 audit calls (login success/failure) |
| `backend/api/documents.py` | +3 audit calls (upload, delete, clear) |
| `backend/api/backup.py` | +2 audit calls (download, restore) |
| `backend/api/users.py` | +3 audit calls (create, update, delete) |
| `backend/middleware/logging.py` | Sanitize Authorization header + query params |
| `backend/main.py` | Register audit router |

## 9. Acceptance Criteria

- Login attempt (success and failure) creates audit records
- Document upload/delete/clear creates audit records with document_id
- Backup download/restore creates audit records
- User create/update/delete creates audit records
- `GET /api/audit/` returns records, filtered by action
- Access logs contain no `Authorization: Bearer xxx` values
- Audit helper never crashes the app (best-effort)
- Existing tests still pass
