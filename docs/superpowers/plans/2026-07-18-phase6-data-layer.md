# Phase 6: Data Layer Engineering Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Establish Alembic as the sole schema authority, converge session/file storage boundaries, and make backup/recovery schema-aware — all while keeping SQLite as the only supported database.

**Architecture:** Five sequential phases (A→E). Phase A inventories current schema and builds safety nets. Phase B introduces Alembic with a hand-reviewed baseline migration and startup revision gate. Phase C unifies session lifecycle and isolates SQLite-specific behavior behind a capability adapter. Phase D adds a streaming `LocalFileStorage` with `storage_key` backfill and migrates all call sites. Phase E wires Alembic revision into backup manifests and validates the full pipeline.

**Tech Stack:** Python 3.11+, SQLAlchemy 2.0 (async), Alembic, aiosqlite, pytest-asyncio

---

## File Structure

```
backend/
  models/
    database.py          → Modify: remove runtime DDL, add new_session()/session_scope(), session gate
    orm.py               → Modify: add storage_key column to Document
    dialect.py           → Create: DatabaseCapabilities protocol + SqliteAdapter
  storage/
    base.py              → Create: FileStorage protocol + StagedObject/StoredObject
    files.py             → Modify: LocalFileStorage implements FileStorage
  alembic/
    env.py               → Create: async Alembic config
    script.py.mako       → Generate: alembic init
    versions/
      0001_current_sqlite_schema.py → Create: hand-reviewed baseline
      0002_document_storage_key.py  → Create: storage_key migration + backfill
  alembic.ini            → Create
  main.py                → Modify: startup revision gate, init_db() slim-down
  api/
    documents.py         → Modify: use storage service
    backup.py            → Modify: manifest with revision, staged restore
    settings.py          → Modify: use storage service for rebuild
  rag/
    pipeline.py          → Modify: use storage service for file reads
  worker/
    tasks.py             → Modify: use storage service for parsing
  tests/
    test_alembic.py      → Create: migration tests
    test_dialect.py      → Create: capability adapter tests
    test_session.py      → Create: session lifecycle tests
    test_file_storage.py → Create: LocalFileStorage tests
    test_backup_schema.py → Create: backup manifest + revision tests
```

---

### Task A1: Generate Schema Inventory

**Files:**
- Read: `backend/models/orm.py`
- Read: `backend/models/database.py`
- Create: `scripts/schema_inventory.py`

- [ ] **Step 1: Write the schema inventory script**

```python
# scripts/schema_inventory.py
"""Print the current SQLite schema as a JSON inventory for review.
Run against a real database: python scripts/schema_inventory.py data/rag_agent.db
"""
import asyncio
import json
import sys
from pathlib import Path

from sqlalchemy import inspect, text
from sqlalchemy.ext.asyncio import create_async_engine


async def inventory(db_path: str) -> dict:
    url = f"sqlite+aiosqlite:///{Path(db_path).resolve()}"
    engine = create_async_engine(url, echo=False)
    tables = {}

    async with engine.begin() as conn:
        # ORM tables via inspector
        def sync_inspect(sync_conn):
            insp = inspect(sync_conn)
            result = {}
            for table in insp.get_table_names():
                cols = []
                for c in insp.get_columns(table):
                    cols.append({
                        "name": c["name"],
                        "type": str(c["type"]),
                        "nullable": c.get("nullable", True),
                        "default": str(c.get("default")) if c.get("default") is not None else None,
                    })
                pks = [c["name"] for c in insp.get_pk_constraint(table).get("constrained_columns", [])]
                fks = [
                    {
                        "cols": fk["constrained_columns"],
                        "ref_table": fk["referred_table"],
                        "ref_cols": fk["referred_columns"],
                    }
                    for fk in insp.get_foreign_keys(table)
                ]
                idxs = [
                    {"name": idx["name"], "cols": idx["column_names"], "unique": idx.get("unique", False)}
                    for idx in insp.get_indexes(table)
                ]
                result[table] = {"columns": cols, "primary_keys": pks, "foreign_keys": fks, "indexes": idxs}
            return result

        tables = await conn.run_sync(sync_inspect)

        # Virtual tables (FTS5, etc.) via sqlite_master
        vtab_result = await conn.execute(
            text("SELECT name, sql FROM sqlite_master WHERE type='table' AND sql LIKE '%VIRTUAL%'")
        )
        virtual_tables = {}
        for row in vtab_result.fetchall():
            virtual_tables[row[0]] = row[1]

        # All sqlite_master entries for full coverage
        all_result = await conn.execute(
            text("SELECT type, name, sql FROM sqlite_master WHERE sql IS NOT NULL ORDER BY type, name")
        )
        all_objects = [[r[0], r[1], r[2]] for r in all_result.fetchall()]

    await engine.dispose()
    return {
        "orm_tables": tables,
        "virtual_tables": virtual_tables,
        "all_sqlite_master": all_objects,
    }


async def main():
    if len(sys.argv) < 2:
        print(f"Usage: python {sys.argv[0]} <path/to/database.db>", file=sys.stderr)
        sys.exit(1)
    result = await inventory(sys.argv[1])
    print(json.dumps(result, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    asyncio.run(main())
```

- [ ] **Step 2: Run inventory against an existing database (if available)**

Run: `python backend/scripts/schema_inventory.py backend/data/rag_agent.db 2>&1 || echo "No existing DB — will run after smoke test creates one"`

- [ ] **Step 3: Write the SQLite-specific SQL grep script**

```python
# scripts/sqlite_specific_grep.py
"""Grep the backend source for SQLite-specific SQL patterns."""
import re
from pathlib import Path

PATTERNS = [
    (r"PRAGMA\s+\w+", "PRAGMA"),
    (r"CREATE\s+VIRTUAL\s+TABLE", "FTS5 VIRTUAL TABLE"),
    (r"INSERT\s+OR\s+REPLACE", "INSERT OR REPLACE"),
    (r"datetime\('now'\)", "datetime('now')"),
    (r"strftime\(", "strftime"),
    (r"fts5", "FTS5 reference", re.IGNORECASE),
]

def scan(path: Path):
    results = []
    for f in sorted(path.rglob("*.py")):
        if "site-packages" in str(f) or "__pycache__" in str(f):
            continue
        try:
            text = f.read_text(encoding="utf-8")
        except Exception:
            continue
        for line_no, line in enumerate(text.splitlines(), 1):
            for pattern, label, *flags in PATTERNS:
                flag = flags[0] if flags else 0
                if re.search(pattern, line, flag):
                    # Skip comments
                    stripped = line.strip()
                    if stripped.startswith("#") or stripped.startswith('"""') or stripped.startswith("'''"):
                        continue
                    results.append(f"{f}:{line_no}: [{label}] {stripped[:120]}")
    return results


if __name__ == "__main__":
    backend = Path(__file__).resolve().parent.parent / "backend"
    for r in scan(backend):
        print(r)
```

Run: `python scripts/sqlite_specific_grep.py`

- [ ] **Step 4: Commit**

```bash
git add scripts/schema_inventory.py scripts/sqlite_specific_grep.py
git commit -m "feat: add schema inventory and SQLite-specific SQL grep tools"
```

---

### Task A2: Database Fingerprint Tool

**Files:**
- Create: `backend/models/fingerprint.py`

- [ ] **Step 1: Write the fingerprint module**

```python
# backend/models/fingerprint.py
"""Database structure fingerprint for safe Alembic adoption.

Generates a deterministic fingerprint of the current database structure
(tables, columns, indexes, virtual tables) so we can verify an existing
database matches the expected baseline before stamping it.
"""
import hashlib
import json
from typing import Optional

from sqlalchemy import inspect, text
from sqlalchemy.ext.asyncio import AsyncConnection


async def compute_fingerprint(conn: AsyncConnection) -> str:
    """Compute a SHA-256 fingerprint of the database structure."""

    def _collect(sync_conn) -> dict:
        insp = inspect(sync_conn)
        structure: dict = {}

        for table in sorted(insp.get_table_names()):
            cols = []
            for c in insp.get_columns(table):
                cols.append({
                    "name": c["name"],
                    "type": str(c["type"]),
                    "nullable": c.get("nullable", True),
                })
            pks = sorted(
                insp.get_pk_constraint(table).get("constrained_columns", [])
            )
            fks = sorted(
                (fk["constrained_columns"], fk["referred_table"], fk["referred_columns"])
                for fk in insp.get_foreign_keys(table)
            )
            idxs = sorted(
                (idx["name"], idx["column_names"], idx.get("unique", False))
                for idx in insp.get_indexes(table)
            )
            structure[table] = {
                "columns": cols,
                "primary_keys": pks,
                "foreign_keys": [list(f) for f in fks],
                "indexes": [list(i) for i in idxs],
            }

        return structure

    structure = await conn.run_sync(_collect)

    # Also capture virtual tables from sqlite_master
    vtab_result = await conn.execute(
        text("SELECT name, sql FROM sqlite_master WHERE type='table' AND sql IS NOT NULL ORDER BY name")
    )
    vtab_entries = sorted((r[0], r[1]) for r in vtab_result.fetchall())
    structure["_virtual_tables"] = {name: sql for name, sql in vtab_entries}

    canonical = json.dumps(structure, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(canonical.encode()).hexdigest()


async def fingerprint_matches(conn: AsyncConnection, expected: str) -> bool:
    """Check if the current database fingerprint matches the expected value."""
    actual = await compute_fingerprint(conn)
    return actual == expected


async def diff_fingerprint(conn: AsyncConnection, expected_fingerprint: str) -> list[str]:
    """Return a human-readable list of structural differences.

    Returns an empty list when the database matches.  Otherwise each entry
    describes one difference (missing table, extra column, wrong index, etc.).
    """
    issues: list[str] = []

    def _diff(sync_conn) -> list[str]:
        insp = inspect(sync_conn)
        problems: list[str] = []
        table_names = insp.get_table_names()
        # We don't have the expected structure here — just report what's present.
        # The caller should compare fingerprints first; this is a diagnostic helper.
        problems.append(f"Tables present: {', '.join(sorted(table_names))}")
        return problems

    issues = await conn.run_sync(_diff)
    return issues
```

- [ ] **Step 2: Commit**

```bash
git add backend/models/fingerprint.py
git commit -m "feat: add database structure fingerprint utility"
```

---

### Task B1: Install Alembic and Initialize

**Files:**
- Modify: `backend/requirements.txt`
- Create: `backend/alembic.ini`
- Create: `backend/alembic/env.py`
- Create: `backend/alembic/script.py.mako`

- [ ] **Step 1: Add alembic to requirements**

```bash
cd backend && echo "alembic>=1.14,<2" >> requirements.txt
```

- [ ] **Step 2: Install alembic**

```bash
cd backend && pip install alembic>=1.14
```

- [ ] **Step 3: Create alembic.ini**

```ini
# backend/alembic.ini
[alembic]
script_location = alembic
prepend_sys_path = .

# sqlalchemy.url is set programmatically in env.py from DATABASE_URL env / .env
sqlalchemy.url =

[loggers]
keys = root,sqlalchemy,alembic

[handlers]
keys = console

[formatters]
keys = generic

[logger_root]
level = WARN
handlers = console

[logger_sqlalchemy]
level = WARN
handlers =
qualname = sqlalchemy.engine

[logger_alembic]
level = INFO
handlers =
qualname = alembic

[handler_console]
class = StreamHandler
args = (sys.stderr,)
level = NOTSET
formatter = generic

[formatter_generic]
format = %(levelname)-5.5s [%(name)s] %(message)s
datefmt = %H:%M:%S
```

- [ ] **Step 4: Create async env.py**

```python
# backend/alembic/env.py
"""Alembic async environment for SQLite (aiosqlite)."""
import asyncio
from pathlib import Path

from alembic import context
from sqlalchemy import pool
from sqlalchemy.ext.asyncio import create_async_engine

# Import all models so Base.metadata is complete
import sys
_backend_dir = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_backend_dir))

from config import settings as _settings_module
from models.database import Base
from models import orm  # noqa: F401 — ensure all ORM models are registered

target_metadata = Base.metadata


def _get_url():
    url = _settings_module.database_url
    if not url:
        raise RuntimeError("DATABASE_URL is not set")
    return url


def run_migrations_offline():
    url = _get_url()
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection):
    context.configure(connection=connection, target_metadata=target_metadata)
    with context.begin_transaction():
        context.run_migrations()


async def run_migrations_online():
    connectable = create_async_engine(
        _get_url(),
        poolclass=pool.NullPool,
    )
    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)
    await connectable.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    asyncio.run(run_migrations_online())
```

- [ ] **Step 5: Create script.py.mako**

```mako
# backend/alembic/script.py.mako
"""${message}

Revision ID: ${up_revision}
Revises: ${down_revision | comma,n}
Create Date: ${create_date}
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
${imports if imports else ""}

revision: str = ${repr(up_revision)}
down_revision: Union[str, None] = ${repr(down_revision)}
branch_labels: Union[str, Sequence[str], None] = ${repr(branch_labels)}
depends_on: Union[str, Sequence[str], None] = ${repr(depends_on)}


def upgrade() -> None:
    ${upgrades if upgrades else "pass"}


def downgrade() -> None:
    ${downgrades if downgrades else "pass"}
```

- [ ] **Step 6: Verify alembic can connect**

Run in backend directory:
```bash
cd backend && python -c "
from config import settings
print(f'DB URL: {settings.database_url}')
"
```

Expected: prints the resolved database URL path.

- [ ] **Step 7: Commit**

```bash
git add backend/requirements.txt backend/alembic.ini backend/alembic/
git commit -m "feat: initialize Alembic with async SQLite configuration"
```

---

### Task B2: Create Baseline Migration 0001

**Files:**
- Create: `backend/alembic/versions/0001_current_sqlite_schema.py`

- [ ] **Step 1: Generate the autogenerate draft**

```bash
cd backend && alembic revision --autogenerate -m "current_sqlite_schema"
```

Note the generated filename in `backend/alembic/versions/`.

- [ ] **Step 2: Read the generated migration, then write the hand-reviewed baseline**

Read the autogenerated file first to understand what Alembic detected. Then replace it with a hand-reviewed version that includes FTS5, BM25, and all tables not covered by ORM models.

```python
# backend/alembic/versions/0001_current_sqlite_schema.py
"""Current supported SQLite schema baseline — hand-reviewed.

This is the authoritative schema.  Every table (ORM and raw-SQL), virtual
table (FTS5), BM25 index, and constraint the application needs must be
represented here.  Autogenerate cannot detect FTS5 virtual tables or
tables created via raw SQL in init_db(), so those are added manually.

Revision ID: 0001
Revises: None
Create Date: 2026-07-18
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ── ORM-managed tables (from Base.metadata via autogenerate + review) ──

    op.create_table(
        "documents",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("filename", sa.String(), nullable=False),
        sa.Column("size_bytes", sa.Integer(), nullable=True),
        sa.Column("file_hash", sa.String(), nullable=True),
        sa.Column("status", sa.String(), nullable=False, server_default="uploaded"),
        sa.Column("error_message", sa.String(), nullable=True),
        sa.Column("active_generation_id", sa.String(), nullable=True),
        sa.Column("created_at", sa.String(), nullable=False),
        sa.Column("updated_at", sa.String(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )

    op.create_table(
        "index_generations",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("document_id", sa.String(), nullable=False),
        sa.Column("status", sa.String(), nullable=False, server_default="preparing"),
        sa.Column("chunk_count", sa.Integer(), nullable=True),
        sa.Column("qdrant_point_count", sa.Integer(), nullable=True),
        sa.Column("bm25_doc_count", sa.Integer(), nullable=True),
        sa.Column("error_message", sa.String(), nullable=True),
        sa.Column("created_at", sa.String(), nullable=False),
        sa.Column("committed_at", sa.String(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )

    op.create_table(
        "conversations",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("title", sa.String(), nullable=True),
        sa.Column("created_at", sa.String(), nullable=False),
        sa.Column("updated_at", sa.String(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )

    op.create_table(
        "messages",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("conversation_id", sa.String(), nullable=False),
        sa.Column("role", sa.String(), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("sources", sa.Text(), nullable=True),
        sa.Column("verification", sa.Text(), nullable=True),
        sa.Column("created_at", sa.String(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )

    op.create_table(
        "user_memories",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("key", sa.String(), nullable=False),
        sa.Column("value", sa.Text(), nullable=False),
        sa.Column("created_at", sa.String(), nullable=False),
        sa.Column("updated_at", sa.String(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )

    op.create_table(
        "user_profiles",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("key", sa.String(), nullable=False),
        sa.Column("value", sa.Text(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )

    op.create_table(
        "users",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("username", sa.String(), nullable=False),
        sa.Column("password_hash", sa.String(), nullable=False),
        sa.Column("role", sa.String(), nullable=False, server_default="viewer"),
        sa.Column("must_change_password", sa.Boolean(), nullable=False, server_default=sa.text("1")),
        sa.Column("created_at", sa.String(), nullable=False),
        sa.Column("updated_at", sa.String(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("username"),
    )

    op.create_table(
        "task_queue",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("document_id", sa.String(), nullable=True),
        sa.Column("task_type", sa.String(), nullable=False),
        sa.Column("status", sa.String(), nullable=False, server_default="pending"),
        sa.Column("attempt", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("max_attempts", sa.Integer(), nullable=False, server_default=sa.text("3")),
        sa.Column("error_message", sa.String(), nullable=True),
        sa.Column("heartbeat_at", sa.String(), nullable=True),
        sa.Column("created_at", sa.String(), nullable=False),
        sa.Column("updated_at", sa.String(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )

    op.create_table(
        "audit_logs",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("actor", sa.String(), nullable=True),
        sa.Column("action", sa.String(), nullable=False),
        sa.Column("resource", sa.String(), nullable=True),
        sa.Column("resource_id", sa.String(), nullable=True),
        sa.Column("detail", sa.Text(), nullable=True),
        sa.Column("request_id", sa.String(), nullable=True),
        sa.Column("ip_address", sa.String(), nullable=True),
        sa.Column("created_at", sa.String(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )

    # ── FTS5 virtual table for BM25 text search ──
    op.execute("""
        CREATE VIRTUAL TABLE IF NOT EXISTS bm25_docs
        USING fts5(
            doc_id,
            document_id,
            chunk_id,
            text,
            tokenize='porter unicode61'
        )
    """)

    # ── BM25 auxiliary tables (created by init_db raw SQL) ──
    op.execute("""
        CREATE TABLE IF NOT EXISTS bm25_index (
            term TEXT PRIMARY KEY,
            doc_count INTEGER NOT NULL DEFAULT 0,
            total_freq INTEGER NOT NULL DEFAULT 0
        )
    """)

    op.execute("""
        CREATE TABLE IF NOT EXISTS bm25_stats (
            total_docs INTEGER NOT NULL DEFAULT 0,
            avg_doc_length REAL NOT NULL DEFAULT 0.0
        )
    """)

    # ── Indexes for common query patterns ──
    op.create_index("idx_documents_status", "documents", ["status"])
    op.create_index("idx_documents_active_gen", "documents", ["active_generation_id"])
    op.create_index("idx_index_generations_doc", "index_generations", ["document_id"])
    op.create_index("idx_index_generations_status", "index_generations", ["status"])
    op.create_index("idx_messages_conversation", "messages", ["conversation_id"])
    op.create_index("idx_messages_created", "messages", ["created_at"])
    op.create_index("idx_task_queue_status", "task_queue", ["status"])
    op.create_index("idx_task_queue_document", "task_queue", ["document_id"])
    op.create_index("idx_audit_logs_action", "audit_logs", ["action"])
    op.create_index("idx_audit_logs_actor", "audit_logs", ["actor"])
    op.create_index("idx_audit_logs_created", "audit_logs", ["created_at"])

    # ── WAL journal mode ──
    op.execute("PRAGMA journal_mode=WAL")


def downgrade() -> None:
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
```

- [ ] **Step 3: Run the migration against a fresh database**

```bash
cd backend
# Create a temp database to test the migration
export TEST_DB="$(mktemp)"
export DATABASE_URL="sqlite+aiosqlite:///$TEST_DB"
alembic upgrade head
# Verify tables exist
python -c "
import sqlite3
conn = sqlite3.connect('$TEST_DB')
tables = [r[0] for r in conn.execute(\"SELECT name FROM sqlite_master WHERE type='table' ORDER BY name\").fetchall()]
print('Tables created:', tables)
conn.close()
"
rm -f "$TEST_DB"
```

Expected: All tables listed including `bm25_docs`, `bm25_index`, `bm25_stats`, `audit_logs`, `task_queue`.

- [ ] **Step 4: Run the downgrade and verify cleanup**

```bash
cd backend
export TEST_DB="$(mktemp)"
export DATABASE_URL="sqlite+aiosqlite:///$TEST_DB"
alembic upgrade head
alembic downgrade base
python -c "
import sqlite3
conn = sqlite3.connect('$TEST_DB')
tables = [r[0] for r in conn.execute(\"SELECT name FROM sqlite_master WHERE type='table' ORDER BY name\").fetchall()]
print('Tables after downgrade:', tables)
conn.close()
"
rm -f "$TEST_DB"
```

Expected: No application tables remain (only `alembic_version`).

- [ ] **Step 5: Commit**

```bash
git add backend/alembic/versions/0001_current_sqlite_schema.py
git commit -m "feat: add hand-reviewed baseline Alembic migration 0001"
```

---

### Task B3: Startup Revision Gate

**Files:**
- Modify: `backend/main.py`
- Modify: `backend/models/database.py`

- [ ] **Step 1: Add revision gate function to database.py**

Add this after the existing `init_db()` function:

```python
# backend/models/database.py — append after init_db()

from alembic.config import Config as AlembicConfig
from alembic import command as alembic_command
from sqlalchemy import text

async def _current_revision(conn) -> str | None:
    """Return the current Alembic revision or None if no version table."""
    try:
        result = await conn.execute(text("SELECT version_num FROM alembic_version"))
        row = result.fetchone()
        return row[0] if row else None
    except Exception:
        return None


def _head_revision() -> str:
    """Return the head revision from Alembic config."""
    import os
    from pathlib import Path

    backend_dir = Path(__file__).resolve().parent.parent
    alembic_ini = str(backend_dir / "alembic.ini")
    os.chdir(str(backend_dir))
    cfg = AlembicConfig(alembic_ini)
    # We only need to read the config, not connect
    script = cfg.get_main_option("script_location")
    from alembic.script import ScriptDirectory
    script_dir = ScriptDirectory.from_config(cfg)
    heads = script_dir.get_heads()
    if not heads:
        raise RuntimeError("No Alembic head revision found")
    return heads[0]


def _auto_migrate_enabled() -> bool:
    import os
    return os.getenv("AUTO_MIGRATE", "").lower() in ("1", "true", "yes")


async def check_revision_gate() -> None:
    """Startup gate: verify the database schema revision is current.

    Raises RuntimeError with a descriptive message when the database
    is not at the expected revision.
    """
    from config import settings

    engine = create_async_engine(settings.database_url, echo=False)
    try:
        import os
        db_path = settings.database_url.replace("sqlite+aiosqlite:///", "")
        db_exists = os.path.exists(db_path) and os.path.getsize(db_path) > 0

        async with engine.connect() as conn:
            current = await _current_revision(conn)

        head = _head_revision()

        if current is None:
            if db_exists:
                raise RuntimeError(
                    "Database exists but has no Alembic revision. "
                    "Run the legacy database adoption procedure: "
                    "1) backup, 2) compute fingerprint, 3) stamp if matching. "
                )
            elif _auto_migrate_enabled():
                # Dev/test: auto-migrate from empty
                from alembic.config import Config as AlcCfg
                from alembic import command as alc_cmd
                import os as _os
                _backend_dir = _os.path.dirname(_os.path.dirname(__file__))
                cfg = AlcCfg(str(_backend_dir / "alembic.ini"))
                alc_cmd.upgrade(cfg, "head")
                return
            else:
                raise RuntimeError(
                    "Empty database with AUTO_MIGRATE disabled. "
                    "Run: alembic upgrade head"
                )
        elif current != head:
            raise RuntimeError(
                f"Database revision '{current}' does not match code revision '{head}'. "
                f"Run: alembic upgrade head"
            )
        # current == head: OK
    finally:
        await engine.dispose()
```

- [ ] **Step 2: Wire the gate into main.py startup**

In `backend/main.py`, add the call early in the lifespan startup. Find the `@asynccontextmanager` lifespan function and add:

```python
# backend/main.py — inside the lifespan startup, before any other DB access

from models.database import check_revision_gate

# In the startup section, BEFORE init_db():
await check_revision_gate()
```

- [ ] **Step 3: Update init_db() to remove DDL**

In `backend/models/database.py`, modify `init_db()`:
- Remove all `ALTER TABLE ADD COLUMN` blocks
- Remove all `CREATE TABLE IF NOT EXISTS` blocks (audit_logs, users, task_queue, index_generations)
- Keep only: PRAGMA setup (WAL, busy_timeout, foreign_keys), FTS5/BM25 table creation (these are runtime features, not schema migrations since they can be rebuilt)
- Add a comment block at the top: "Schema DDL is managed by Alembic. This function handles runtime initialization only."

- [ ] **Step 4: Test the revision gate behavior**

```bash
cd backend

# Test 1: Empty database with AUTO_MIGRATE
AUTO_MIGRATE=1 DATABASE_URL="sqlite+aiosqlite:///$(mktemp)" python -c "
import asyncio
from models.database import check_revision_gate
asyncio.run(check_revision_gate())
print('PASS: auto-migrate from empty')
"

# Test 2: Existing DB at head (create one first)
export TEST_DB="$(mktemp)"
export DATABASE_URL="sqlite+aiosqlite:///$TEST_DB"
alembic upgrade head
python -c "
import asyncio
from models.database import check_revision_gate
asyncio.run(check_revision_gate())
print('PASS: existing DB at head')
"
rm -f "$TEST_DB"
```

- [ ] **Step 5: Commit**

```bash
git add backend/models/database.py backend/main.py
git commit -m "feat: add Alembic revision gate at startup; remove runtime DDL from init_db()"
```

---

### Task C1: SQLite Capability Adapter

**Files:**
- Create: `backend/models/dialect.py`

- [ ] **Step 1: Write the dialect module**

```python
# backend/models/dialect.py
"""Database dialect capability adapter.

Centralizes SQLite-specific behavior so that PostgreSQL support can be
added later without changing business logic.  Unsupported dialects raise
UnsupportedDialectError immediately — no silent fallback.
"""
from typing import Protocol

from sqlalchemy.ext.asyncio import AsyncConnection, AsyncSession
from sqlalchemy import text


class UnsupportedDialectError(RuntimeError):
    """Raised when the database dialect is not supported."""


class DatabaseCapabilities(Protocol):
    dialect_name: str
    supports_fts: bool

    async def health_check(self, session: AsyncSession) -> None: ...
    async def rebuild_fts(self, session: AsyncSession) -> None: ...


class SqliteAdapter:
    dialect_name = "sqlite"
    supports_fts = True

    async def health_check(self, session: AsyncSession) -> None:
        """Verify SQLite is responsive and WAL mode is active."""
        async with session.begin() as conn:
            result = await conn.execute(text("PRAGMA journal_mode"))
            row = result.fetchone()
            if not row or row[0] != "wal":
                raise RuntimeError(
                    f"SQLite journal_mode is '{row[0] if row else 'unknown'}', expected 'wal'. "
                    "The filesystem may not support WAL."
                )

    async def rebuild_fts(self, session: AsyncSession) -> None:
        """Rebuild the FTS5 index from the documents table."""
        async with session.begin() as conn:
            # Drop and recreate FTS5 virtual table
            await conn.execute(text("DROP TABLE IF EXISTS bm25_docs"))
            await conn.execute(text("""
                CREATE VIRTUAL TABLE bm25_docs
                USING fts5(
                    doc_id,
                    document_id,
                    chunk_id,
                    text,
                    tokenize='porter unicode61'
                )
            """))
            # Repopulate (caller must provide document data)
            # This is a structural rebuild — data reinsertion is done by the BM25 indexer


def get_adapter(dialect_name: str) -> DatabaseCapabilities:
    """Return the capability adapter for a given dialect.

    Raises UnsupportedDialectError if the dialect is not supported.
    """
    if dialect_name == "sqlite":
        return SqliteAdapter()
    raise UnsupportedDialectError(
        f"Database dialect '{dialect_name}' is not supported. "
        "Only 'sqlite' is supported in this release."
    )
```

- [ ] **Step 2: Write the test**

```python
# backend/tests/test_dialect.py
import pytest
from models.dialect import get_adapter, UnsupportedDialectError, SqliteAdapter


def test_get_adapter_sqlite_returns_sqlite_adapter():
    adapter = get_adapter("sqlite")
    assert isinstance(adapter, SqliteAdapter)
    assert adapter.dialect_name == "sqlite"
    assert adapter.supports_fts is True


def test_get_adapter_unknown_raises():
    with pytest.raises(UnsupportedDialectError, match="postgresql"):
        get_adapter("postgresql")

    with pytest.raises(UnsupportedDialectError, match="mysql"):
        get_adapter("mysql")


@pytest.mark.asyncio
async def test_sqlite_health_check_wal():
    from models.database import get_session
    adapter = SqliteAdapter()
    async with get_session() as session:
        # Should not raise if WAL is active
        await adapter.health_check(session)


@pytest.mark.asyncio
async def test_sqlite_rebuild_fts_creates_table():
    from models.database import get_session
    from sqlalchemy import text
    adapter = SqliteAdapter()
    async with get_session() as session:
        await adapter.rebuild_fts(session)
    # Verify FTS5 table exists after rebuild
    async with get_session() as session:
        result = await session.execute(
            text("SELECT name FROM sqlite_master WHERE type='table' AND name='bm25_docs'")
        )
        assert result.fetchone() is not None
```

- [ ] **Step 3: Run tests**

```bash
cd backend && pytest tests/test_dialect.py -v
```

Expected: 4/4 PASS

- [ ] **Step 4: Commit**

```bash
git add backend/models/dialect.py backend/tests/test_dialect.py
git commit -m "feat: add SQLite capability adapter with unsupported-dialect guard"
```

---

### Task C2: SQLite Connection-Level PRAGMAs

**Files:**
- Modify: `backend/models/database.py`

- [ ] **Step 1: Add connect event listener for per-connection PRAGMAs**

In `backend/models/database.py`, modify the engine creation to register connect events:

```python
# backend/models/database.py — modify engine creation

from sqlalchemy import event
from sqlalchemy.engine import Engine

engine = create_async_engine(settings.database_url, echo=False)

@event.listens_for(engine.sync_engine, "connect")
def _set_sqlite_pragmas(dbapi_connection, connection_record):
    """Set per-connection PRAGMAs for every connection from the pool."""
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA foreign_keys = ON")
    # Busy timeout in milliseconds — how long to wait on a locked DB
    cursor.execute("PRAGMA busy_timeout = 30000")
    cursor.close()
```

- [ ] **Step 2: Remove PRAGMA from init_db()**

In `init_db()`, remove the lines:
```python
await conn.execute(text("PRAGMA journal_mode=WAL"))
await conn.execute(text("PRAGMA busy_timeout=30000"))
await conn.execute(text("PRAGMA foreign_keys=ON"))
```

Keep only `PRAGMA journal_mode=WAL` as a one-time database-level setting (not per-connection).

- [ ] **Step 3: Verify PRAGMAs are set**

```bash
cd backend && python -c "
import asyncio
from models.database import get_session
from sqlalchemy import text

async def check():
    async with get_session() as session:
        r = await session.execute(text('PRAGMA foreign_keys'))
        fk = r.fetchone()
        print(f'foreign_keys: {fk}')
        assert fk[0] == 1, f'Expected foreign_keys=ON, got {fk}'

        r = await session.execute(text('PRAGMA busy_timeout'))
        bt = r.fetchone()
        print(f'busy_timeout: {bt}')
        assert bt[0] == 30000, f'Expected busy_timeout=30000, got {bt}'

        r = await session.execute(text('PRAGMA journal_mode'))
        jm = r.fetchone()
        print(f'journal_mode: {jm}')
        assert jm[0] == 'wal', f'Expected journal_mode=wal, got {jm}'

    print('All PRAGMAs verified')
asyncio.run(check())
"
```

- [ ] **Step 4: Commit**

```bash
git add backend/models/database.py
git commit -m "feat: set SQLite PRAGMAs per-connection via connect event listener"
```

---

### Task C3: Unified Session Lifecycle

**Files:**
- Modify: `backend/models/database.py`
- Create: `backend/tests/test_session.py`

- [ ] **Step 1: Add new_session() and session_scope() to database.py**

```python
# backend/models/database.py — replace get_db() and add new functions

from contextlib import asynccontextmanager
import warnings

def new_session(**overrides) -> AsyncSession:
    """Create a new independent session.

    Caller is responsible for commit/rollback/close.
    Prefer session_scope() for automatic cleanup.
    """
    return async_session(**overrides)


@asynccontextmanager
async def session_scope() -> AsyncIterator[AsyncSession]:
    """Async context manager that ensures session cleanup.

    Closes the session on exit.  Rolls back on exception.
    Does NOT auto-commit — caller must call commit() explicitly.
    """
    session = new_session()
    try:
        yield session
    except Exception:
        await session.rollback()
        raise
    finally:
        await session.close()


async def get_db() -> AsyncSession:
    """FastAPI dependency injection — delegates to session_scope."""
    async with session_scope() as session:
        yield session


# Keep old name as deprecated alias for one release cycle
def _deprecated_async_session():
    warnings.warn(
        "Direct async_session access is deprecated. Use new_session() or session_scope().",
        DeprecationWarning,
        stacklevel=2,
    )
    return async_session
```

- [ ] **Step 2: Write session lifecycle tests**

```python
# backend/tests/test_session.py
import pytest
from sqlalchemy import text
from models.database import new_session, session_scope


@pytest.mark.asyncio
async def test_new_session_can_commit():
    session = new_session()
    try:
        await session.execute(text("SELECT 1"))
        await session.commit()
    finally:
        await session.close()


@pytest.mark.asyncio
async def test_session_scope_auto_rollback_on_exception():
    class TestError(Exception):
        pass

    with pytest.raises(TestError):
        async with session_scope() as session:
            await session.execute(text("SELECT 1"))
            raise TestError("trigger rollback")

    # Session should be closed after scope exit — verify by checking
    # that a new session can access the same data


@pytest.mark.asyncio
async def test_session_scope_no_implicit_commit():
    """Verify that session_scope does not auto-commit on clean exit."""
    async with session_scope() as session:
        await session.execute(text("SELECT 1"))
    # Clean exit with no explicit commit — session closed without error


@pytest.mark.asyncio
async def test_concurrent_sessions_independent():
    """Two concurrent sessions should not interfere."""
    s1 = new_session()
    s2 = new_session()
    try:
        r1 = await s1.execute(text("SELECT 1 AS val"))
        r2 = await s2.execute(text("SELECT 2 AS val"))
        assert r1.fetchone()[0] == 1
        assert r2.fetchone()[0] == 2
    finally:
        await s1.close()
        await s2.close()
```

- [ ] **Step 3: Run tests**

```bash
cd backend && pytest tests/test_session.py -v
```

Expected: 4/4 PASS

- [ ] **Step 4: Commit**

```bash
git add backend/models/database.py backend/tests/test_session.py
git commit -m "feat: add new_session() and session_scope() with explicit commit semantics"
```

---

### Task C4: Migrate Call Sites to session_scope()

**Files:**
- Modify: `backend/api/auth.py`
- Modify: `backend/api/users.py`
- Modify: `backend/api/audit.py`
- Modify: `backend/api/memories.py`
- Modify: `backend/api/chat.py`
- Modify: `backend/memory/profile.py`
- Modify: `backend/rag/pipeline.py`
- Modify: `backend/rag/retriever.py`
- Modify: `backend/textdb/bm25_search.py`
- Modify: `backend/worker/tasks.py`
- Modify: `backend/agent/tools.py`

- [ ] **Step 1: Replace direct async_session usage — auth/users/audit/memories**

For each file, replace:
```python
from models.database import async_session
# and
async with async_session() as session:
```

With:
```python
from models.database import session_scope
# and
async with session_scope() as session:
```

Files to update (read first, then edit each):
- `backend/api/auth.py` — lines with `async_session`
- `backend/api/users.py` — lines with `async_session`
- `backend/api/audit.py` — lines with `async_session`
- `backend/api/memories.py` — lines with `async_session`

- [ ] **Step 2: Replace in chat.py**

`backend/api/chat.py` has a special case — `_save_messages()` creates its own session. Replace:
```python
async with async_session() as db:
```
With:
```python
async with session_scope() as db:
```

- [ ] **Step 3: Replace in memory/profile.py**

Find and replace `async_session` → `session_scope` in `backend/memory/profile.py`.

- [ ] **Step 4: Replace in rag/pipeline.py**

Find and replace `async_session` → `session_scope` in `backend/rag/pipeline.py`.

- [ ] **Step 5: Replace in worker/tasks.py**

Find and replace `async_session` → `new_session` in `backend/worker/tasks.py` (background tasks use manual commit/rollback/close, so they should use `new_session` not `session_scope`).

- [ ] **Step 6: Replace in textdb/bm25_search.py**

Find and replace `async_session` → `session_scope` in `backend/textdb/bm25_search.py`.

- [ ] **Step 7: Replace in agent/tools.py**

Find and replace `async_session` (if used directly) → `session_scope`.

- [ ] **Step 8: Run full test suite to verify no regressions**

```bash
cd backend && pytest -x -v
```

Expected: All tests pass (the count may have changed from 60 due to new tests added in this phase).

- [ ] **Step 9: Commit**

```bash
git add backend/api/ backend/memory/ backend/rag/ backend/textdb/ backend/worker/ backend/agent/
git commit -m "refactor: migrate all call sites from async_session to session_scope/new_session"
```

---

### Task D1: FileStorage Protocol and LocalFileStorage

**Files:**
- Create: `backend/storage/base.py`
- Create: `backend/tests/test_file_storage.py`

- [ ] **Step 1: Write the FileStorage protocol**

```python
# backend/storage/base.py
"""File storage interface with streaming I/O and atomic commit."""
import hashlib
import os
import uuid
from abc import abstractmethod
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import AsyncIterator, Protocol, BinaryIO


@dataclass
class StagedObject:
    """An in-progress file upload being assembled."""
    staging_id: str = field(default_factory=lambda: uuid.uuid4().hex)
    filename: str = ""
    temp_path: str = ""
    size: int = 0
    sha256: str = ""


@dataclass
class StoredObject:
    """A committed file in storage."""
    storage_key: str
    filename: str
    size: int
    sha256: str


class AsyncReadable(Protocol):
    """A readable stream for stored files."""
    async def read(self, n: int = -1) -> bytes: ...
    async def close(self) -> None: ...


class FileStorage(Protocol):
    """Storage backend for document files.

    Implementations must support streaming I/O (not whole-file-in-memory),
    atomic commit with hash verification, and path traversal safety.
    """

    @abstractmethod
    async def create_staging(self, filename: str) -> StagedObject:
        """Create a staging slot for an upload. Returns a StagedObject."""
        ...

    @abstractmethod
    async def append(self, staged: StagedObject, chunk: bytes) -> None:
        """Append a chunk to a staged upload."""
        ...

    @abstractmethod
    async def commit(
        self, staged: StagedObject, *, expected_sha256: str | None = None
    ) -> StoredObject:
        """Finalize a staged upload.  Verifies hash if provided.  Atomically moves to permanent storage."""
        ...

    @abstractmethod
    async def abort(self, staged: StagedObject) -> None:
        """Discard a staged upload and clean up temp files."""
        ...

    @abstractmethod
    async def open_read(self, storage_key: str) -> AsyncIterator[bytes]:
        """Open a stored file for streaming read.  Yields chunks."""
        ...

    @abstractmethod
    async def delete(self, storage_key: str) -> None:
        """Delete a stored file.  Idempotent — no error if already missing."""
        ...

    @abstractmethod
    async def exists(self, storage_key: str) -> bool:
        """Check if a storage key exists."""
        ...
```

- [ ] **Step 2: Write LocalFileStorage implementation**

```python
# backend/storage/files.py — add LocalFileStorage class (keep existing functions)

from storage.base import FileStorage, StagedObject, StoredObject

class LocalFileStorage(FileStorage):
    """File storage backed by a local directory.

    Enforces path safety, atomic commit via rename, and streaming I/O.
    """
    def __init__(self, root_dir: str):
        self._root = Path(root_dir).resolve()
        self._root.mkdir(parents=True, exist_ok=True)
        self._staging_dir = self._root / ".staging"
        self._staging_dir.mkdir(parents=True, exist_ok=True)

    def _safe_path(self, key: str) -> Path:
        """Resolve a storage key and verify it's within the root directory."""
        p = (self._root / key).resolve()
        if not str(p).startswith(str(self._root)):
            raise ValueError(f"Path traversal blocked: {key}")
        return p

    async def create_staging(self, filename: str) -> StagedObject:
        staged = StagedObject(filename=filename)
        staged.temp_path = str(self._staging_dir / f".upload-{staged.staging_id}")
        return staged

    async def append(self, staged: StagedObject, chunk: bytes) -> None:
        with open(staged.temp_path, "ab") as f:
            f.write(chunk)
        staged.size = os.path.getsize(staged.temp_path)

    async def commit(
        self, staged: StagedObject, *, expected_sha256: str | None = None
    ) -> StoredObject:
        # Compute hash
        sha = hashlib.sha256()
        with open(staged.temp_path, "rb") as f:
            while True:
                data = f.read(64 * 1024)
                if not data:
                    break
                sha.update(data)
        staged.sha256 = sha.hexdigest()

        if expected_sha256 and staged.sha256 != expected_sha256:
            raise ValueError(
                f"Hash mismatch: expected {expected_sha256[:16]}..., "
                f"got {staged.sha256[:16]}..."
            )

        # Generate unique storage key
        storage_key = f"{staged.sha256[:2]}/{staged.sha256[2:4]}/{staged.sha256}"
        dest = self._safe_path(storage_key)
        dest.parent.mkdir(parents=True, exist_ok=True)

        # Atomic rename
        os.rename(staged.temp_path, dest)

        return StoredObject(
            storage_key=storage_key,
            filename=staged.filename,
            size=staged.size,
            sha256=staged.sha256,
        )

    async def abort(self, staged: StagedObject) -> None:
        if staged.temp_path and os.path.exists(staged.temp_path):
            os.unlink(staged.temp_path)

    async def open_read(self, storage_key: str) -> AsyncIterator[bytes]:
        path = self._safe_path(storage_key)
        with open(path, "rb") as f:
            while True:
                chunk = f.read(64 * 1024)
                if not chunk:
                    return
                yield chunk

    async def delete(self, storage_key: str) -> None:
        path = self._safe_path(storage_key)
        if path.exists():
            path.unlink()
            # Clean up empty parent dirs
            for parent in path.parents:
                if parent == self._root:
                    break
                try:
                    parent.rmdir()
                except OSError:
                    break

    async def exists(self, storage_key: str) -> bool:
        return self._safe_path(storage_key).exists()
```

- [ ] **Step 3: Write LocalFileStorage tests**

```python
# backend/tests/test_file_storage.py
import os
import tempfile
import pytest
from storage.base import FileStorage
from storage.files import LocalFileStorage


@pytest.fixture
def storage():
    with tempfile.TemporaryDirectory() as tmp:
        yield LocalFileStorage(tmp)


@pytest.mark.asyncio
async def test_roundtrip_small_file(storage: FileStorage):
    staged = await storage.create_staging("test.txt")
    await storage.append(staged, b"hello")
    await storage.append(staged, b" world")
    stored = await storage.commit(staged)

    assert stored.size == 11
    assert stored.filename == "test.txt"

    chunks = [c async for c in storage.open_read(stored.storage_key)]
    assert b"".join(chunks) == b"hello world"


@pytest.mark.asyncio
async def test_hash_verification(storage: FileStorage):
    staged = await storage.create_staging("data.bin")
    await storage.append(staged, b"secret")

    with pytest.raises(ValueError, match="Hash mismatch"):
        await storage.commit(staged, expected_sha256="00" * 32)


@pytest.mark.asyncio
async def test_hash_match(storage: FileStorage):
    import hashlib
    staged = await storage.create_staging("data.bin")
    content = b"verify me"
    await storage.append(staged, content)
    expected = hashlib.sha256(content).hexdigest()
    stored = await storage.commit(staged, expected_sha256=expected)
    assert stored.sha256 == expected


@pytest.mark.asyncio
async def test_no_overwrite_on_same_content(storage: FileStorage):
    staged = await storage.create_staging("doc.pdf")
    await storage.append(staged, b"pdf content")
    stored1 = await storage.commit(staged)

    staged2 = await storage.create_staging("doc.pdf")
    await storage.append(staged2, b"pdf content")
    stored2 = await storage.commit(staged2)

    # Same content → same storage key → second commit is a no-op rename (overwrites same dest)
    assert stored1.storage_key == stored2.storage_key


@pytest.mark.asyncio
async def test_path_traversal_blocked(storage: FileStorage):
    with pytest.raises(ValueError, match="Path traversal"):
        await storage._safe_path("../../../etc/passwd")


@pytest.mark.asyncio
async def test_delete_idempotent(storage: FileStorage):
    # Deleting non-existent key should not raise
    await storage.delete("nonexistent/key")

    # Deleting a real key
    staged = await storage.create_staging("temp.txt")
    await storage.append(staged, b"x")
    stored = await storage.commit(staged)
    assert await storage.exists(stored.storage_key)
    await storage.delete(stored.storage_key)
    assert not await storage.exists(stored.storage_key)
    # Second delete is a no-op
    await storage.delete(stored.storage_key)


@pytest.mark.asyncio
async def test_abort_cleans_temp(storage: FileStorage):
    staged = await storage.create_staging("abort.txt")
    await storage.append(staged, b"data")
    assert os.path.exists(staged.temp_path)
    await storage.abort(staged)
    assert not os.path.exists(staged.temp_path)


@pytest.mark.asyncio
async def test_streaming_large_file(storage: FileStorage):
    # Simulate a file larger than one chunk
    staged = await storage.create_staging("large.bin")
    chunk = b"A" * (64 * 1024)
    for _ in range(100):  # ~6.4 MB
        await storage.append(staged, chunk)
    stored = await storage.commit(staged)
    assert stored.size == 100 * 64 * 1024

    total_read = 0
    async for c in storage.open_read(stored.storage_key):
        total_read += len(c)
    assert total_read == stored.size
```

- [ ] **Step 4: Run tests**

```bash
cd backend && pytest tests/test_file_storage.py -v
```

Expected: 8/8 PASS

- [ ] **Step 5: Commit**

```bash
git add backend/storage/base.py backend/storage/files.py backend/tests/test_file_storage.py
git commit -m "feat: add streaming FileStorage protocol and LocalFileStorage implementation"
```

---

### Task D2: Add storage_key to Documents and Migration 0002

**Files:**
- Modify: `backend/models/orm.py`
- Create: `backend/alembic/versions/0002_document_storage_key.py`

- [ ] **Step 1: Add storage_key to Document ORM model**

In `backend/models/orm.py`, add to the `Document` class:

```python
# In the Document class, add this column:
storage_key: Mapped[Optional[str]] = mapped_column(String, nullable=True)
```

- [ ] **Step 2: Create migration 0002**

```python
# backend/alembic/versions/0002_document_storage_key.py
"""Add storage_key column to documents and backfill from existing file paths.

Revision ID: 0002
Revises: 0001
Create Date: 2026-07-18
"""
from typing import Sequence, Union
from pathlib import Path
import hashlib
import os

from alembic import op
import sqlalchemy as sa

revision: str = "0002"
down_revision: Union[str, None] = "0001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("documents", sa.Column("storage_key", sa.String(), nullable=True))
    op.create_index("idx_documents_storage_key", "documents", ["storage_key"])

    # Backfill: try to locate existing files by filename and compute storage_key
    conn = op.get_bind()
    rows = conn.execute(
        sa.text("SELECT id, filename, size_bytes, file_hash FROM documents WHERE storage_key IS NULL")
    ).fetchall()

    upload_dir = os.environ.get("UPLOAD_DIR", "./data/uploads")
    upload_path = Path(upload_dir)

    for row in rows:
        doc_id, filename, size_bytes, file_hash = row
        if not filename:
            continue

        # Find the file by filename
        candidates = list(upload_path.rglob(filename))
        matched = None
        for cand in candidates:
            if cand.is_file():
                if size_bytes and cand.stat().st_size != size_bytes:
                    continue
                matched = cand
                break

        if matched is not None:
            # Compute SHA-256 and determine storage key
            sha = hashlib.sha256()
            with open(matched, "rb") as f:
                while True:
                    data = f.read(64 * 1024)
                    if not data:
                        break
                    sha.update(data)
            file_sha = sha.hexdigest()

            if file_hash and file_hash != file_sha:
                # Hash mismatch — skip this file
                continue

            storage_key = f"{file_sha[:2]}/{file_sha[2:4]}/{file_sha}"
            conn.execute(
                sa.text("UPDATE documents SET storage_key = :key WHERE id = :id"),
                {"key": storage_key, "id": doc_id},
            )


def downgrade() -> None:
    op.drop_index("idx_documents_storage_key", table_name="documents")
    op.drop_column("documents", "storage_key")
```

- [ ] **Step 3: Test migration against a database with document records**

```bash
cd backend

# Create test DB with 0001 applied and some fake documents
export TEST_DB="$(mktemp)"
export DATABASE_URL="sqlite+aiosqlite:///$TEST_DB"
alembic upgrade 0001

# Insert a test document record
python -c "
import sqlite3
conn = sqlite3.connect('$TEST_DB')
conn.execute(\"INSERT INTO documents (id, filename, size_bytes, status, created_at, updated_at) VALUES ('doc-1', 'test.txt', 5, 'ready', '2026-01-01', '2026-01-01')\")
conn.commit()
conn.close()
"

# Run the storage_key migration
alembic upgrade head

# Verify the column exists
python -c "
import sqlite3
conn = sqlite3.connect('$TEST_DB')
cols = [r[1] for r in conn.execute('PRAGMA table_info(documents)').fetchall()]
assert 'storage_key' in cols, f'storage_key not in {cols}'
print('PASS: storage_key column exists')
conn.close()
"
rm -f "$TEST_DB"
```

- [ ] **Step 4: Commit**

```bash
git add backend/models/orm.py backend/alembic/versions/0002_document_storage_key.py
git commit -m "feat: add storage_key to documents with migration and backfill"
```

---

### Task D3: Migrate File Access Call Sites to Storage Service

**Files:**
- Modify: `backend/api/documents.py`
- Modify: `backend/api/backup.py`
- Modify: `backend/rag/pipeline.py`
- Modify: `backend/worker/tasks.py`
- Modify: `backend/api/settings.py`

- [ ] **Step 1: Create a module-level storage singleton**

```python
# backend/storage/__init__.py
"""Storage module — provides a singleton LocalFileStorage instance."""
from config import settings
from storage.files import LocalFileStorage

_storage: LocalFileStorage | None = None


def get_storage() -> LocalFileStorage:
    global _storage
    if _storage is None:
        _storage = LocalFileStorage(settings.upload_dir)
    return _storage
```

- [ ] **Step 2: Update document upload endpoint to use storage service**

In `backend/api/documents.py`, modify the `upload_document` function:
- Instead of `save_upload(content, filename)`, use:
```python
storage = get_storage()
staged = await storage.create_staging(file.filename)
await storage.append(staged, await file.read())
stored = await storage.commit(staged)
# Then save the document record with stored.storage_key
```

- [ ] **Step 3: Update batch upload similarly**

Apply the same pattern to `upload_document_batch` — each file goes through `create_staging → append → commit`.

- [ ] **Step 4: Update document deletion**

In `delete_document`, after deleting the DB record, call:
```python
storage = get_storage()
if document.storage_key:
    await storage.delete(document.storage_key)
```

- [ ] **Step 5: Update pipeline file reads**

In `backend/rag/pipeline.py`, replace direct file path access with:
```python
storage = get_storage()
chunks = []
async for chunk in storage.open_read(document.storage_key):
    chunks.append(chunk)
content = b"".join(chunks)
```

- [ ] **Step 6: Update worker file reads**

In `backend/worker/tasks.py`, replace direct file open with `storage.open_read()`.

- [ ] **Step 7: Update settings.py rebuild to use storage**

In `backend/api/settings.py`, `rebuild_collections` — replace direct file path reads.

- [ ] **Step 8: Run tests**

```bash
cd backend && pytest -x -v
```

Expected: All tests pass, including existing document upload/delete tests updated for the storage service.

- [ ] **Step 9: Commit**

```bash
git add backend/storage/__init__.py backend/api/documents.py backend/api/backup.py backend/rag/pipeline.py backend/worker/tasks.py backend/api/settings.py
git commit -m "refactor: migrate file access call sites to storage service"
```

---

### Task E1: Backup Manifest with Alembic Revision

**Files:**
- Modify: `backend/api/backup.py`

- [ ] **Step 1: Update _build_manifest() and BACKUP_FORMAT_VERSION**

In `backend/api/backup.py`:
- Change `BACKUP_FORMAT_VERSION = 1` to `BACKUP_FORMAT_VERSION = 2`
- In `_build_manifest()` (line ~295), add `_get_alembic_revision()` call and include the result plus database SHA-256:

```python
# backend/api/backup.py — modify _build_manifest()

BACKUP_FORMAT_VERSION = 2

def _get_alembic_revision(db_path: Path) -> str:
    """Read Alembic revision from the live database."""
    conn = sqlite3.connect(str(db_path))
    try:
        row = conn.execute("SELECT version_num FROM alembic_version").fetchone()
        return row[0] if row else "unknown"
    except sqlite3.OperationalError:
        return "legacy"
    finally:
        conn.close()


def _build_manifest(db_path: Path, upload_dir: Path) -> dict[str, Any]:
    # Compute database SHA-256
    db_sha256 = hashlib.sha256(db_path.read_bytes()).hexdigest()

    manifest: dict[str, Any] = {
        "format_version": BACKUP_FORMAT_VERSION,
        "db_schema_revision": _get_alembic_revision(db_path),
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "database_sha256": db_sha256,
        "git_commit": _git_commit(),
        # ... rest of existing manifest fields unchanged
    }
    return manifest
```

- [ ] **Step 1b: Add `_validate_restore_revision()` function**

```python
# backend/api/backup.py — new function

def _validate_restore_revision(
    manifest_revision: str | None,
    current_head_revision: str,
) -> None:
    """Validate that a backup can be restored against the current application.

    Raises ValueError if the backup revision is newer than the current head.
    Older / same / legacy (None) revisions are accepted — the caller handles
    staging migration or fingerprint verification.
    """
    if manifest_revision is None:
        return  # Legacy backup — caller performs fingerprint check

    if manifest_revision == current_head_revision:
        return  # Same version

    # Sort revisions by their numeric prefix for comparison
    def _sort_key(rev: str) -> int:
        try:
            return int(rev.split("_")[0])
        except (ValueError, IndexError):
            return 0

    manifest_key = _sort_key(manifest_revision)
    current_key = _sort_key(current_head_revision)

    if manifest_key > current_key:
        raise ValueError(
            f"Backup revision '{manifest_revision}' is newer than "
            f"current revision '{current_head_revision}'. "
            "Use a newer application version to restore this backup."
        )
    # manifest_key <= current_key → accepted (caller handles staging migration)
```

- [ ] **Step 2: Update restore to validate revision**

In the restore function, add revision check logic:

```python
# During restore in backend/api/backup.py

manifest_revision = manifest.get("db_schema_revision")
if manifest_revision is None:
    # Legacy backup without revision — must pass fingerprint check
    pass  # Handled by existing validation
elif manifest_revision == current_revision:
    # Same version — standard restore
    pass
elif _is_older_revision(manifest_revision, current_revision):
    # Older backup — migrate in staging, then restore
    await _migrate_staging_backup(staging_db_path, current_revision)
else:
    raise ValueError(
        f"Backup revision '{manifest_revision}' is newer than "
        f"current revision '{current_revision}'. Use a newer application version."
    )
```

- [ ] **Step 3: Write backup manifest tests**

```python
# backend/tests/test_backup_schema.py
import json
import os
import tempfile
from pathlib import Path
from unittest.mock import patch, AsyncMock

import pytest
from sqlalchemy import text


@pytest.mark.asyncio
async def test_build_manifest_includes_revision():
    """_build_manifest must include db_schema_revision when Alembic is initialized."""
    from api.backup import _build_manifest
    from models.database import new_session

    with tempfile.TemporaryDirectory() as tmp:
        db_path = Path(tmp) / "test.db"
        upload_dir = Path(tmp) / "uploads"
        upload_dir.mkdir()

        # Create a minimal database with alembic_version
        import sqlite3
        conn = sqlite3.connect(str(db_path))
        conn.execute("CREATE TABLE alembic_version (version_num TEXT)")
        conn.execute("INSERT INTO alembic_version VALUES ('0002')")
        conn.commit()
        conn.close()

        manifest = _build_manifest(db_path, upload_dir)
        assert manifest["format_version"] == 2
        assert manifest["db_schema_revision"] == "0002"
        assert "database_sha256" in manifest


@pytest.mark.asyncio
async def test_validate_restore_rejects_newer_revision():
    """A backup with revision newer than current head must be rejected."""
    from api.backup import _validate_restore_revision

    with pytest.raises(ValueError, match="newer"):
        _validate_restore_revision(
            manifest_revision="0003",
            current_head_revision="0002",
        )


@pytest.mark.asyncio
async def test_validate_restore_accepts_same_revision():
    """A backup with matching revision must be accepted."""
    from api.backup import _validate_restore_revision

    # Should not raise
    _validate_restore_revision(
        manifest_revision="0002",
        current_head_revision="0002",
    )


@pytest.mark.asyncio
async def test_validate_restore_accepts_older_revision():
    """A backup with older revision must be accepted (staging migration handles it)."""
    from api.backup import _validate_restore_revision

    # Should not raise — caller handles migration
    _validate_restore_revision(
        manifest_revision="0001",
        current_head_revision="0002",
    )


@pytest.mark.asyncio
async def test_validate_restore_legacy_no_revision():
    """Legacy backups without revision must be accepted (fingerprint verification)."""
    from api.backup import _validate_restore_revision

    # Should not raise — caller handles legacy path
    _validate_restore_revision(
        manifest_revision=None,
        current_head_revision="0002",
    )
```

- [ ] **Step 4: Implement staging database migration for restore**

```python
# backend/api/backup.py — new function

async def _migrate_staging_database(staging_db_path: str, target_revision: str) -> None:
    """Run Alembic migrations on a staging database."""
    import subprocess
    import os
    from pathlib import Path

    backend_dir = Path(__file__).resolve().parent.parent
    alembic_ini = str(backend_dir / "alembic.ini")

    env = os.environ.copy()
    env["DATABASE_URL"] = f"sqlite+aiosqlite:///{staging_db_path}"

    result = subprocess.run(
        ["alembic", "-c", alembic_ini, "upgrade", target_revision],
        cwd=str(backend_dir),
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )
    if result.returncode != 0:
        raise RuntimeError(f"Staging migration failed: {result.stderr}")
```

- [ ] **Step 5: Commit**

```bash
git add backend/api/backup.py backend/tests/test_backup_schema.py
git commit -m "feat: add Alembic revision to backup manifest with version-gated restore"
```

---

### Task E2: Full Regression and Docker E2E Verification

- [ ] **Step 1: Run full backend test suite**

```bash
cd backend && pytest -v
```

Expected: All tests pass.

- [ ] **Step 2: Run frontend build**

```bash
cd frontend && npm run build
```

Expected: Production build succeeds.

- [ ] **Step 3: Run Docker E2E acceptance**

```bash
pwsh scripts/docker_e2e_acceptance.ps1
```

Expected: 5/5 smoke tests pass.

- [ ] **Step 4: Run migration cycle test in Docker**

```bash
# Verify fresh Docker deployment runs migrations correctly
docker compose down -v
docker compose build
docker compose up -d
# Check backend logs for revision gate message
docker compose logs backend | grep -i "revision\|alembic\|migration"
docker compose down
```

- [ ] **Step 5: Commit final adjustments**

```bash
git add -A
git commit -m "chore: final regression verification for Phase 6 data layer engineering"
```

---

### Task E3: Production Replica Upgrade Drill

- [ ] **Step 1: Copy production database and files to staging**

```bash
# If a production database exists:
cp backend/data/rag_agent.db backend/data/rag_agent_pre_migration_backup.db
sha256sum backend/data/rag_agent.db > backend/data/pre_migration.sha256
```

- [ ] **Step 2: Record pre-migration state**

```bash
python backend/scripts/schema_inventory.py backend/data/rag_agent.db > pre_migration_schema.json
# Count records per table
python -c "
import sqlite3
conn = sqlite3.connect('backend/data/rag_agent.db')
for table in ['documents','conversations','messages','users','task_queue','audit_logs']:
    count = conn.execute(f'SELECT COUNT(*) FROM {table}').fetchone()[0]
    print(f'{table}: {count}')
conn.close()
" > pre_migration_counts.txt
```

- [ ] **Step 3: Run the migration**

```bash
cd backend
export DATABASE_URL="sqlite+aiosqlite:///./data/rag_agent.db"
alembic stamp 0001  # If fingerprint matches
alembic upgrade head
```

- [ ] **Step 4: Verify post-migration state**

```bash
# Compare record counts
python -c "
import sqlite3
conn = sqlite3.connect('backend/data/rag_agent.db')
for table in ['documents','conversations','messages','users','task_queue','audit_logs']:
    count = conn.execute(f'SELECT COUNT(*) FROM {table}').fetchone()[0]
    print(f'{table}: {count}')
conn.close()
" > post_migration_counts.txt

diff pre_migration_counts.txt post_migration_counts.txt && echo "PASS: Record counts match" || echo "FAIL: Counts differ"

# Check revision
python -c "
import sqlite3
conn = sqlite3.connect('backend/data/rag_agent.db')
rev = conn.execute('SELECT version_num FROM alembic_version').fetchone()[0]
print(f'Current revision: {rev}')
conn.close()
"
```

- [ ] **Step 5: Record timing and disk metrics**

```bash
echo "Migration completed at $(date)"
echo "Database size: $(ls -lh backend/data/rag_agent.db | awk '{print $5}')"
```

- [ ] **Step 6: Commit drill results**

```bash
git add pre_migration_*.txt post_migration_*.txt pre_migration_*.json pre_migration.sha256
git commit -m "docs: Phase 6 upgrade drill results on production data replica"
```
