# RBAC + JWT Authentication — Design (Phase 5a)

> Date: 2026-07-17
> Phase: 5a (per `NEXT_PHASE_OPTIMIZATION_EXECUTION_PLAN_2026-07-17.md`)
> Status: approved, ready for implementation planning

## 1. Goal

Replace the single admin token with role-based access control backed by JWT. Introduce user management (CRUD) for system_admins. Preserve backward compatibility with legacy X-Admin-Token during a configurable transition period.

## 2. Scope

### In
- `users` table with bcrypt password hashing
- `/api/auth/login` — username+password → JWT (access + refresh token)
- `/api/auth/refresh` — refresh token → new access token
- `/api/auth/me` — current user info from JWT
- JWT middleware — verify signature, extract claims, inject `request.state.user`
- 4 role model — viewer, editor, knowledge_admin, system_admin
- Role enforcement replacing `require_admin` at route level
- `/api/users/` CRUD — system_admin only: list, create, update-role, disable, delete
- Legacy token compatibility — `LEGACY_ADMIN_TOKEN_ENABLED=true` flag, emits deprecation warning
- Bootstrap: first-start creates a default `system_admin` user if `users` table is empty

### Out
- Tenant / workspace isolation (Phase 5c/6)
- OIDC / OAuth2 IdP integration (Phase 5c)
- Frontend login page (Phase 5b)
- Password reset / email verification
- Document-level or knowledge-base-level fine-grained permissions
- PostgreSQL migration (Phase 6)

## 3. Data Model

### 3.1 users table

```sql
CREATE TABLE users (
    id TEXT PRIMARY KEY,                  -- UUID
    username TEXT NOT NULL UNIQUE,
    password_hash TEXT NOT NULL,          -- bcrypt
    role TEXT NOT NULL DEFAULT 'viewer',  -- viewer|editor|knowledge_admin|system_admin
    disabled INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    last_login_at TEXT
);
```

Single table, no tenants yet. Migration in `models/database.py` `init_db()`.

### 3.2 ORM Model

```python
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
    created_at: Mapped[datetime] = ...
    last_login_at: Mapped[datetime | None] = ...
```

## 4. Permission Matrix

| Route group | viewer | editor | knowledge_admin | system_admin |
|---|---|---|---|---|
| `/api/chat` | ✓ | ✓ | ✓ | ✓ |
| `/api/conversations` | own only | own only | ✓ | ✓ |
| `/api/documents` (list) | ✓ | ✓ | ✓ | ✓ |
| `/api/documents` (upload/delete) | — | ✓ | ✓ | ✓ |
| `/api/documents/clear-all` | — | — | ✓ | ✓ |
| `/api/documents/reprocess` | — | ✓ | ✓ | ✓ |
| `/api/backup` | — | — | — | ✓ |
| `/api/backup/restore` | — | — | — | ✓ |
| `/api/settings` | — | — | — | ✓ |
| `/api/metrics` | — | — | — | ✓ |
| `/api/users/*` | — | — | — | ✓ |
| `/api/health` | no auth | no auth | no auth | no auth |
| `/api/auth/*` | no auth | no auth | no auth | no auth |

## 5. Auth Flow

### 5.1 Login

```
POST /api/auth/login  {"username": "...", "password": "..."}
  → SELECT user WHERE username = ? AND disabled = 0
  → bcrypt.verify(password, user.password_hash)
  → JWT {sub: user.id, username, role, exp, iat, type: "access"}
  → refresh_token = random 64-char hex (stored in memory or users table)
  → response: {access_token, refresh_token, user: {id, username, role}}
```

Access token TTL: 1 hour. Refresh token TTL: 7 days.

### 5.2 JWT Middleware

```python
# security.py — replaces current require_admin

async def jwt_auth(request: Request) -> None:
    """FastAPI middleware: verify JWT, inject request.state.user."""
    token = _extract_bearer(request)
    if not token:
        if _legacy_admin_token(request):
            return  # transitional
        raise HTTPException(401, "Missing authorization")
    try:
        payload = jwt.decode(token, settings.jwt_secret, algorithms=["HS256"])
    except jwt.ExpiredSignatureError:
        raise HTTPException(401, "Token expired")
    except jwt.InvalidTokenError:
        raise HTTPException(401, "Invalid token")
    
    request.state.user = UserContext(
        user_id=payload["sub"],
        username=payload["username"],
        role=payload["role"],
    )

def require_role(*roles: str):
    """Dependency: enforce minimum role."""
    async def _enforce(request: Request):
        if not hasattr(request.state, "user"):
            raise HTTPException(401, "Not authenticated")
        if request.state.user.role not in roles:
            raise HTTPException(403, "Insufficient permissions")
    return _enforce
```

## 6. Configuration

```python
# config.py additions
jwt_secret: str = ""                           # auto-generated if empty (same pattern as secret_key)
jwt_access_token_expire_minutes: int = 60      # 1 hour
jwt_refresh_token_expire_days: int = 7         # 7 days
legacy_admin_token_enabled: bool = True        # transition period, default on
```

`jwt_secret` auto-generated on first start if not configured (same pattern as `secret_key`).

## 7. User Management API

```
GET    /api/users/         → list all users (system_admin)
POST   /api/users/         → create user {username, password, role} (system_admin)
PATCH  /api/users/{id}     → update {role?, disabled?} (system_admin)
DELETE /api/users/{id}     → delete user (system_admin)
```

Cannot disable or delete yourself. Initial bootstrap user cannot be deleted.

## 8. Migration Path

```
Before:  all routes → Depends(require_admin) → checks X-Admin-Token
Phase 5a: all routes → Depends(jwt_auth) then Depends(require_role(R))
          + legacy X-Admin-Token accepted (deprecation warning in logs)
After:    all routes → Depends(jwt_auth) then Depends(require_role(R))
          + X-Admin-Token disabled (LEGACY_ADMIN_TOKEN_ENABLED=false)
```

`main.py` router-level dependencies change from:
```python
app.include_router(documents_router, dependencies=[Depends(require_admin)])
```
To:
```python
app.include_router(documents_router, dependencies=[Depends(jwt_auth)])
```
Individual routes add `Depends(require_role(...))` for operations above their base level.

## 9. Files

| File | Change |
|---|---|
| `backend/config.py` | 4 new settings |
| `backend/models/orm.py` | User ORM model |
| `backend/models/database.py` | users table migration |
| `backend/security.py` | Rewrite: JWT auth + role enforce + legacy compat |
| `backend/main.py` | New router wiring, bootstrap user |
| `backend/api/auth.py` | New: login, refresh, me |
| `backend/api/users.py` | New: user CRUD |
| `backend/tests/api/test_auth.py` | Extend: JWT + role tests |
| `backend/requirements.txt` | Add `PyJWT>=2.9,<3` |

## 10. Acceptance Criteria

- Login with correct credentials → 200 + JWT + refresh token
- Login with wrong password → 401
- Disabled user login → 401
- JWT expired → 401 with "Token expired"
- JWT with invalid signature → 401
- viewer can chat, list docs → 200
- viewer cannot upload → 403
- editor can upload/delete docs → 200
- editor cannot access /api/users → 403
- system_admin can CRUD users → 200
- system_admin cannot delete self → 400
- Legacy X-Admin-Token works when enabled, emits warning
- Bootstrap user created on first start
- All existing auth tests pass (with legacy mode)
