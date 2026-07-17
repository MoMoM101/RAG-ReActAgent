# Audit Logging & Log Sanitization — Implementation Plan (Phase 5b)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Record every high-risk operation with actor identity and result; sanitize access logs; provide audit query API for system_admins.

**Architecture:** New `audit.py` helper (write-only), new `api/audit.py` (read-only query), migration for `audit_logs` table. Each protected endpoint gets 1-2 lines of `await record_audit(...)`. Log sanitization in existing middleware.

**Tech Stack:** Python 3.12+, SQLite (raw SQL), FastAPI

---

### Task 1: Audit table migration + audit helper

**Files:**
- Modify: `backend/models/database.py` (add migration)
- Create: `backend/audit.py`

- [ ] **Step 1: Add audit_logs migration**

In `backend/models/database.py`, in `init_db()`, add:

```python
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
```

- [ ] **Step 2: Create audit helper**

`backend/audit.py`:

```python
"""Audit logging — record high-risk operations with actor identity."""

import logging

from sqlalchemy import text as sa_text

from models.database import async_session

logger = logging.getLogger(__name__)


async def record_audit(
    action: str,
    object_type: str = "",
    object_id: str = "",
    result: str = "success",
    detail: str = "",
    *,
    actor_id: str = "anonymous",
    actor_username: str = "anonymous",
    request_id: str = "",
) -> None:
    """Write an audit record. Best-effort — never raises."""
    try:
        async with async_session() as session:
            conn = await session.connection()
            await conn.execute(sa_text(
                "INSERT INTO audit_logs "
                "(actor_id, actor_username, action, object_type, object_id, result, detail, request_id) "
                "VALUES (:aid, :aun, :act, :ot, :oid, :res, :det, :rid)"
            ), {
                "aid": actor_id,
                "aun": actor_username,
                "act": action,
                "ot": object_type,
                "oid": object_id,
                "res": result,
                "det": detail,
                "rid": request_id,
            })
            await session.commit()
    except Exception as e:
        logger.warning("audit record failed action=%s: %s", action, e)


def audit_from_request(request, action: str, **kwargs):
    """Convenience: extract actor and request_id from FastAPI Request."""
    import asyncio

    user = getattr(request.state, "user", None)
    actor_id = user.user_id if user else "anonymous"
    actor_username = user.username if user else "anonymous"
    rid = getattr(request.state, "request_id", "")
    return record_audit(
        action=action,
        actor_id=actor_id,
        actor_username=actor_username,
        request_id=rid,
        **kwargs,
    )
```

- [ ] **Step 3: Verify**

```bash
cd D:/Python/subject1/RAG_Agent/backend
python -m py_compile audit.py && python -m py_compile models/database.py && echo "OK"
```

- [ ] **Step 4: Commit**

```bash
git add backend/models/database.py backend/audit.py
git commit -m "feat: add audit_logs table migration and record_audit helper"
```

---

### Task 2: Wire audit calls at high-risk endpoints

**Files:**
- Modify: `backend/api/auth.py`
- Modify: `backend/api/documents.py`
- Modify: `backend/api/backup.py`
- Modify: `backend/api/users.py`

- [ ] **Step 1: Auth API — login**

In `backend/api/auth.py`, add import:
```python
from audit import audit_from_request
```

In `login()`, after successful authentication (before return):
```python
        await audit_from_request(request, "login_success",
                                 object_id=user.id, detail=f"user={user.username}")
```

In `login()`, at the 401 error paths, add:
```python
        await audit_from_request(request, "login_failure", result="failure",
                                 detail=f"username={req.username}")
```
Note: for the 401 paths, `request` may not be the FastAPI Request — pass `actor_id="anonymous"` and skip `audit_from_request`. Use a plain `record_audit` call:
```python
        await record_audit("login_failure", result="failure",
                           detail=f"username={req.username}")
```

- [ ] **Step 2: Documents API — upload, delete, clear**

In `backend/api/documents.py`, add import:
```python
from audit import audit_from_request
```

In upload handler, after successful upload, add:
```python
        await audit_from_request(request, "document_upload",
                                 object_type="document", object_id=doc_id,
                                 detail=f"filename={filename}")
```

In upload-batch handler, after successful batch: add `audit_from_request(request, "document_upload_batch", detail=f"count={succeeded}")`

In delete handler, after successful delete: add `audit_from_request(request, "document_delete", object_type="document", object_id=id)`

In clear-all handler, after successful clear: add `audit_from_request(request, "document_clear_all", detail=f"count={result['count']}")`

- [ ] **Step 3: Backup API — download, restore**

In `backend/api/backup.py`, add import and audit calls:
```python
from audit import audit_from_request

# In backup download: audit_from_request(request, "backup_download")
# In restore: audit_from_request(request, "backup_restore", detail=f"docs={restored}")
```

- [ ] **Step 4: Users API — create, update, delete**

In `backend/api/users.py`, add import and 3 audit calls:
```python
from audit import audit_from_request

# In create_user: audit_from_request(request, "user_create", object_type="user", object_id=user.id, detail=f"username={user.username}, role={user.role}")
# In update_user: audit_from_request(request, "user_update", object_type="user", object_id=user_id)
# In delete_user: audit_from_request(request, "user_delete", object_type="user", object_id=user_id)
```

- [ ] **Step 5: Verify all files compile**

```bash
cd D:/Python/subject1/RAG_Agent/backend
for f in api/auth.py api/documents.py api/backup.py api/users.py; do
    python -m py_compile "$f" && echo "$f OK"
done
```

- [ ] **Step 6: Commit**

```bash
git add backend/api/auth.py backend/api/documents.py backend/api/backup.py backend/api/users.py
git commit -m "feat: wire audit logging to 11 high-risk endpoints across auth, documents, backup, users"
```

---

### Task 3: Log sanitization

**Files:**
- Modify: `backend/middleware/logging.py`

- [ ] **Step 1: Sanitize sensitive data in access logs**

In `_log_request()`, add sanitization:

```python
def _sanitize_path(path: str) -> str:
    """Mask sensitive query parameters."""
    import re
    for key in ("token", "secret", "key", "api_key", "password"):
        path = re.sub(rf"({key}=)[^&\s]+", r"\1***", path, flags=re.IGNORECASE)
    return path
```

In the `record` dict, replace `"path": path` with `"path": _sanitize_path(path)`.

Also explicitly skip logging of Authorization header and request body (the middleware already doesn't log headers/body, but add a comment noting this is intentional).

- [ ] **Step 2: Verify**

```bash
cd D:/Python/subject1/RAG_Agent/backend
python -m py_compile middleware/logging.py && echo "OK"
```

- [ ] **Step 3: Commit**

```bash
git add backend/middleware/logging.py
git commit -m "feat: sanitize sensitive query params in access logs"
```

---

### Task 4: Audit query API

**Files:**
- Create: `backend/api/audit.py`
- Modify: `backend/main.py` (register router)

- [ ] **Step 1: Create audit query API**

`backend/api/audit.py`:

```python
"""Audit log query API — system_admin only."""

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy import text as sa_text

from models.database import async_session
from security import get_current_user, require_role

router = APIRouter(prefix="/api/audit", tags=["audit"])


@router.get("/")
async def list_audit_logs(
    request: Request,
    action: str = Query(default=""),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    _auth: None = Depends(get_current_user),
    _enforce: None = Depends(require_role("system_admin")),
):
    async with async_session() as session:
        conn = await session.connection()
        if action:
            rows = (await conn.execute(sa_text(
                "SELECT id, actor_id, actor_username, action, object_type, "
                "object_id, result, detail, request_id, created_at "
                "FROM audit_logs WHERE action=:action "
                "ORDER BY id DESC LIMIT :limit OFFSET :offset"
            ), {"action": action, "limit": limit, "offset": offset})).fetchall()
        else:
            rows = (await conn.execute(sa_text(
                "SELECT id, actor_id, actor_username, action, object_type, "
                "object_id, result, detail, request_id, created_at "
                "FROM audit_logs ORDER BY id DESC LIMIT :limit OFFSET :offset"
            ), {"limit": limit, "offset": offset})).fetchall()

        return [
            {
                "id": r[0],
                "actor_id": r[1],
                "actor_username": r[2],
                "action": r[3],
                "object_type": r[4],
                "object_id": r[5],
                "result": r[6],
                "detail": r[7],
                "request_id": r[8],
                "created_at": r[9],
            }
            for r in rows
        ]
```

- [ ] **Step 2: Register in main.py**

After the users router registration, add:
```python
from api.audit import router as audit_router
app.include_router(audit_router)
```

- [ ] **Step 3: Verify**

```bash
cd D:/Python/subject1/RAG_Agent/backend
python -m py_compile api/audit.py && python -m py_compile main.py && echo "OK"
```

- [ ] **Step 4: Commit**

```bash
git add backend/api/audit.py backend/main.py
git commit -m "feat: add audit log query API (system_admin only)"
```
