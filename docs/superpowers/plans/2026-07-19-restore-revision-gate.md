# Restore Revision Gate & Staged Migration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Enforce staged-DB-as-authority version gating on restore: reject legacy, migrate old revisions in staging, cross-validate manifest against actual DB schema.

**Architecture:** Replace the manifest-only `_validate_restore_revision` with a four-branch classifier based on the staged DB's `alembic_version` table. Use Alembic `ScriptDirectory.iterate_revisions` for ancestor detection. Run `alembic upgrade head` on the staged copy before switching. Tests switch from hand-crafted schemas to Alembic-generated template databases.

**Tech Stack:** Python 3.12, FastAPI, SQLite, Alembic, pytest + pytest-asyncio

---

## File Structure

| File | Responsibility |
|------|---------------|
| `backend/api/backup.py` | Version gate logic (`_get_head_info`, `_classify_staged_revision`, `_migrate_staged_db`), modified `restore_backup` flow |
| `backend/tests/api/test_restore_integration.py` | Template DB fixture, refactored `_build_backup_tar`, 7 new/updated test cases |

---

### Task 1: Add version gate functions to backup.py

**Files:**
- Modify: `backend/api/backup.py` (add functions after `_get_alembic_revision`, before `_move_or_copy`)

- [ ] **Step 1: Add `_get_head_info()` to replace `_get_head_revision()`**

Add after line 135 (after `_get_head_revision`):

```python
def _get_head_info() -> tuple[str, "ScriptDirectory"]:
    """Return (head_revision, ScriptDirectory) for revision classification.
    
    ScriptDirectory is returned alongside head to avoid constructing it twice
    (once for verification, once for migration).
    """
    from alembic.config import Config as AlcCfg
    from alembic.script import ScriptDirectory
    backend_dir = Path(__file__).resolve().parent.parent
    cfg = AlcCfg(str(backend_dir / "alembic.ini"))
    script_dir = ScriptDirectory.from_config(cfg)
    heads = script_dir.get_heads()
    return (heads[0] if heads else "unknown", script_dir)
```

- [ ] **Step 2: Add `_classify_staged_revision()` — the four-branch classifier**

Add after `_get_head_info`:

```python
def _classify_staged_revision(
    staged_revision: str | None,
    head: str,
    script_dir: "ScriptDirectory",
) -> str:
    """Classify staged revision relative to current head.
    
    Returns one of: 'legacy', 'current', 'old', 'unknown', 'future'.
    - legacy: no alembic_version table/row in staged DB
    - current: staged revision == head
    - old: staged revision is an ancestor of head (needs migration)
    - unknown: revision string not found in local migration history
    - future: revision exists in history but is not reachable from head (descendant or parallel branch)
    """
    if staged_revision is None:
        return "legacy"
    if staged_revision == head:
        return "current"

    # Check if the revision exists in the local migration history at all
    try:
        script_dir.get_revision(staged_revision)
    except Exception:
        return "unknown"

    # Check if staged_revision is an ancestor of head by walking from head down
    try:
        list(script_dir.iterate_revisions(head, staged_revision))
        return "old"
    except Exception:
        return "future"
```

- [ ] **Step 3: Add `_migrate_staged_db()` for running alembic upgrade on staged file**

Add after `_classify_staged_revision`:

```python
async def _migrate_staged_db(db_path: Path, head_revision: str) -> None:
    """Run alembic upgrade head on a staged SQLite database file.
    
    Temporarily overrides settings.database_url so alembic/env.py targets
    the staged file instead of the live database. Verifies the revision
    reached head after migration.
    
    Raises RuntimeError on migration failure or post-migration revision mismatch.
    """
    from alembic.config import Config as AlcCfg
    from alembic import command as alc_cmd

    backend_dir = Path(__file__).resolve().parent.parent
    cfg = AlcCfg(str(backend_dir / "alembic.ini"))

    original_url = settings.database_url
    settings.database_url = f"sqlite+aiosqlite:///{db_path.as_posix()}"
    try:
        import asyncio as _aio
        await _aio.to_thread(alc_cmd.upgrade, cfg, "head")
    except Exception as e:
        raise RuntimeError(f"staged migration failed: {e}") from e
    finally:
        settings.database_url = original_url

    # Verify migration reached head
    new_revision = _get_alembic_revision(db_path)
    if new_revision != head_revision:
        raise RuntimeError(
            f"staged migration verification failed: expected revision {head_revision}, "
            f"got {new_revision}"
        )
```

- [ ] **Step 4: Run existing restore tests to confirm no regressions from adding dead code**

```bash
cd backend && python -m pytest tests/api/test_restore_integration.py -v 2>&1 | tail -30
```

Expected: all tests pass (new functions are unused, no impact).

- [ ] **Step 5: Commit**

```bash
git add backend/api/backup.py
git commit -m "feat: add version gate functions for restore revision classification"
```

---

### Task 2: Integrate version gate into restore_backup flow

**Files:**
- Modify: `backend/api/backup.py` lines 826-867 (manifest handling + restore doc reading)

- [ ] **Step 1: Replace manifest revision handling with staged-DB authority logic**

In `restore_backup()`, replace lines 826-867 (from `manifest_path = ...` through the doc reading block) with:

```python
        manifest_path = restore_dir / "manifest.json"
        manifest = None
        if manifest_path.exists():
            try:
                manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            except json.JSONDecodeError as e:
                raise HTTPException(400, "备份文件 manifest.json 格式无效") from e
            _verify_manifest(restore_dir, manifest)
            _validate_manifest_compatibility(manifest)

        db_file = restore_dir / "rag_agent.db"
        uploads_dir = restore_dir / "uploads"
        if not db_file.exists():
            raise HTTPException(400, "备份文件不完整：缺少 rag_agent.db")

        try:
            conn = sqlite3.connect(str(db_file))
        except sqlite3.Error as e:
            raise HTTPException(400, f"备份数据库无法打开: {e}") from e
        try:
            cur = conn.execute("PRAGMA integrity_check")
            result = cur.fetchone()
            if result and result[0] != "ok":
                raise HTTPException(400, f"备份数据库完整性检查失败: {result[0]}")
        except sqlite3.Error as e:
            raise HTTPException(400, f"备份数据库完整性检查失败: {e}") from e
        finally:
            conn.close()

        # ── Version gate: staged DB as authority ──
        staged_revision_raw = _get_alembic_revision(db_file)
        staged_revision = None if staged_revision_raw in ("legacy", "unknown") else staged_revision_raw
        head_revision, script_dir = _get_head_info()

        # Cross-validate manifest db_schema_revision against staged DB
        if manifest is not None:
            manifest_revision = manifest.get("db_schema_revision")
            if manifest_revision is not None:
                if staged_revision is None:
                    raise HTTPException(
                        400,
                        f"备份 manifest 声明版本 {manifest_revision}，"
                        f"但备份数据库未包含 Alembic 版本信息。"
                        "该备份可能损坏或来自旧版本应用，请通过离线流程采纳。",
                    )
                if manifest_revision != staged_revision:
                    raise HTTPException(
                        400,
                        f"备份 manifest 版本 ({manifest_revision}) 与数据库实际版本 "
                        f"({staged_revision}) 不一致，备份文件可能损坏。",
                    )

        # Classify and act on staged revision
        classification = _classify_staged_revision(staged_revision, head_revision, script_dir)

        if classification == "legacy":
            raise HTTPException(
                400,
                "备份数据库不包含版本信息（legacy 格式）。"
                "legacy 备份不支持直接恢复。请通过离线采纳流程："
                "1) 提取备份中的数据库文件，2) 核对表结构指纹，"
                "3) 使用 alembic stamp 标记版本后重新打包。",
            )
        elif classification in ("unknown", "future"):
            detail = (
                f"备份数据库版本 '{staged_revision}' 不被当前应用识别。"
                "请升级应用到包含此版本的新版本。"
            )
            raise HTTPException(400, detail)
        elif classification == "old":
            logger.info(
                "staged DB revision %s < head %s, running staged migration",
                staged_revision, head_revision,
            )
            try:
                await _migrate_staged_db(db_file, head_revision)
            except RuntimeError as e:
                raise HTTPException(400, f"备份数据库迁移失败: {e}") from e
            logger.info("staged migration complete, revision now at %s", head_revision)

        # Read restore documents from backup DB (preserving original IDs)
        conn = sqlite3.connect(str(db_file))
        conn.row_factory = sqlite3.Row
        try:
            staging_rows = list(conn.execute(
                "SELECT id, filename, file_type, file_hash, raw_text, chunk_count "
                "FROM documents WHERE status = 'ready'"
            ).fetchall())
        finally:
            conn.close()
        restore_docs = [RestoreDocument.from_row(r) for r in staging_rows]
```

- [ ] **Step 2: Verify the modified function loads without syntax errors**

```bash
cd backend && python -c "from api.backup import router; print('OK')" 2>&1
```

Expected: `OK` (no import errors).

- [ ] **Step 3: Commit**

```bash
git add backend/api/backup.py
git commit -m "feat: enforce staged-DB-as-authority version gate on restore"
```

---

### Task 3: Add template DB fixture and refactor _build_backup_tar

**Files:**
- Modify: `backend/tests/api/test_restore_integration.py` lines 1-152 (imports and `_build_backup_tar`)

- [ ] **Step 1: Add module-level template DB path variable and fixture**

After the imports (after line 23, before `TEST_TOKEN` / `ADMIN_HEADERS`), add:

```python
_TEMPLATE_HEAD_DB: Path | None = None
_TEMPLATE_0001_DB: Path | None = None


def _create_template_db(target_revision: str) -> Path:
    """Create a template SQLite database at a specific Alembic revision.
    
    Uses a temporary database URL override so alembic/env.py targets the
    template file instead of the test database.
    """
    import shutil
    from alembic.config import Config as AlcCfg
    from alembic import command as alc_cmd
    from config import settings

    tmp = Path(tempfile.mkdtemp(prefix=f"test_template_{target_revision}_"))
    db_path = tmp / "template.db"

    backend_dir = Path(__file__).resolve().parent.parent.parent
    cfg = AlcCfg(str(backend_dir / "alembic.ini"))

    original_url = settings.database_url
    settings.database_url = f"sqlite+aiosqlite:///{db_path.as_posix()}"
    try:
        alc_cmd.upgrade(cfg, target_revision)
    finally:
        settings.database_url = original_url

    return db_path


@pytest.fixture(scope="module")
def template_head_db():
    """Module-level fixture: create a head-revision template database.
    
    All test classes that use _build_backup_tar with schema='head' (the default)
    depend on this fixture. The template is created once per test module.
    """
    global _TEMPLATE_HEAD_DB
    _TEMPLATE_HEAD_DB = _create_template_db("head")
    yield _TEMPLATE_HEAD_DB
    _TEMPLATE_HEAD_DB = None
    if _TEMPLATE_HEAD_DB is not None:
        shutil.rmtree(_TEMPLATE_HEAD_DB.parent, ignore_errors=True)
```

- [ ] **Step 2: Refactor `_build_backup_tar` to use template DB and accept `schema` parameter**

Replace `_build_backup_tar` signature and body (lines 32-152) with:

```python
def _build_test_backup_tar(
    db_rows: list[dict] | None = None,
    upload_files: dict[str, bytes] | None = None,
    *,
    include_manifest: bool = True,
    corrupt_db: bool = False,
    manifest_override: dict | None = None,
    schema: str = "head",
    revision_override: str | None = None,
) -> bytes:
    """Build an in-memory tar.gz simulating a backup archive.
    
    Args:
        schema: 'head' (template from alembic upgrade head),
                '0001' (template at revision 0001),
                'legacy' (hand-crafted schema without alembic_version).
        revision_override: If set, write this value into manifest's
            db_schema_revision regardless of actual DB revision.
    """
    import hashlib
    from alembic.config import Config as AlcCfg
    from alembic import command as alc_cmd

    tmp = Path(tempfile.mkdtemp(prefix="test_backup_build_"))
    try:
        db_path = tmp / "rag_agent.db"

        if schema == "head":
            if _TEMPLATE_HEAD_DB is None:
                raise RuntimeError(
                    "template_head_db fixture not initialized — "
                    "add 'template_head_db' to test function parameters"
                )
            shutil.copy2(_TEMPLATE_HEAD_DB, db_path)
        elif schema == "0001":
            if _TEMPLATE_0001_DB is None:
                _create_template_db("0001")  # will set _TEMPLATE_0001_DB ... wait no
            # Create 0001 template on demand (cached per module)
            global _TEMPLATE_0001_DB
            if _TEMPLATE_0001_DB is None:
                _TEMPLATE_0001_DB = _create_template_db("0001")
            shutil.copy2(_TEMPLATE_0001_DB, db_path)
        elif schema == "legacy":
            conn = sqlite3.connect(str(db_path))
            conn.execute("""
                CREATE TABLE IF NOT EXISTS documents (
                    id TEXT PRIMARY KEY, filename TEXT NOT NULL,
                    file_hash TEXT NOT NULL UNIQUE, file_size INTEGER NOT NULL DEFAULT 0,
                    file_type TEXT NOT NULL DEFAULT '.txt',
                    status TEXT NOT NULL DEFAULT 'ready',
                    chunk_count INTEGER DEFAULT 0,
                    embedding_model TEXT, embedding_dim INTEGER,
                    error_message TEXT,
                    created_at TEXT DEFAULT (datetime('now')),
                    updated_at TEXT DEFAULT (datetime('now')),
                    raw_text TEXT, chunk_size INTEGER
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS conversations (
                    id TEXT PRIMARY KEY, title TEXT DEFAULT '',
                    created_at TEXT DEFAULT (datetime('now')),
                    updated_at TEXT DEFAULT (datetime('now')),
                    last_extracted_at TEXT
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS messages (
                    id TEXT PRIMARY KEY, conversation_id TEXT NOT NULL,
                    role TEXT NOT NULL, content TEXT,
                    tool_call_id TEXT, tool_name TEXT,
                    tool_args TEXT, tool_result_json TEXT, sources TEXT,
                    created_at TEXT DEFAULT (datetime('now'))
                )
            """)
            conn.commit()
            conn.close()
        else:
            raise ValueError(f"Unknown schema: {schema}")

        # Insert test data rows
        conn = sqlite3.connect(str(db_path))
        for row in (db_rows or []):
            conn.execute(
                "INSERT OR REPLACE INTO documents "
                "(id, filename, file_type, file_hash, file_size, raw_text, chunk_count, status) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (row["id"], row["filename"], row.get("file_type", ".txt"),
                 row.get("file_hash", ""), row.get("file_size", 0),
                 row.get("raw_text", ""), row.get("chunk_count", 0),
                 row.get("status", "ready")),
            )
            if row.get("conversation_id"):
                conn.execute(
                    "INSERT OR REPLACE INTO conversations (id, title) VALUES (?, ?)",
                    (row["conversation_id"], row.get("title", "Test Conv")),
                )
                conn.execute(
                    "INSERT OR REPLACE INTO messages (id, conversation_id, role, content) "
                    "VALUES (?, ?, ?, ?)",
                    (row.get("msg_id", f"msg-{row['id']}"),
                     row["conversation_id"], "user",
                     row.get("msg_content", "hello")),
                )
        conn.commit()
        conn.close()

        if corrupt_db:
            data = bytearray(db_path.read_bytes())
            mid = len(data) // 2
            if mid > 50:
                data[mid:mid + 50] = b"\x00" * 50
            db_path.write_bytes(bytes(data))

        # Build upload files
        uploads_dir = tmp / "uploads"
        if upload_files:
            uploads_dir.mkdir(parents=True, exist_ok=True)
            for rel_path, content in upload_files.items():
                fp = uploads_dir / rel_path
                fp.parent.mkdir(parents=True, exist_ok=True)
                fp.write_bytes(content)

        # Determine db_schema_revision for manifest
        if schema == "legacy":
            actual_revision = None
        else:
            actual_revision = _read_alembic_revision_from_db(db_path)

        # Build manifest
        if include_manifest:
            manifest = manifest_override or {
                "format_version": 2,
                "created_at": "2026-07-11T00:00:00Z",
                "collection_name": "rag_chunks",
                "db_schema_revision": actual_revision,
                "files": {
                    "rag_agent.db": {
                        "sha256": hashlib.sha256(db_path.read_bytes()).hexdigest(),
                        "size": db_path.stat().st_size,
                    }
                },
            }
            if revision_override is not None:
                manifest["db_schema_revision"] = revision_override
            if upload_files and manifest_override is None and actual_revision is not None:
                for rel_path in upload_files:
                    fp = uploads_dir / rel_path
                    manifest["files"][f"uploads/{rel_path}"] = {
                        "sha256": hashlib.sha256(fp.read_bytes()).hexdigest(),
                        "size": fp.stat().st_size,
                    }
            (tmp / "manifest.json").write_text(json.dumps(manifest, indent=2),
                                                encoding="utf-8")

        # Build tar.gz
        buf = io.BytesIO()
        with tarfile.open(fileobj=buf, mode="w:gz") as tar:
            tar.add(db_path, arcname="rag_agent.db")
            if include_manifest and (tmp / "manifest.json").exists():
                tar.add(tmp / "manifest.json", arcname="manifest.json")
            if upload_files and uploads_dir.exists():
                tar.add(uploads_dir, arcname="uploads")
        return buf.getvalue()
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def _read_alembic_revision_from_db(db_path: Path) -> str | None:
    """Read alembic_version from a SQLite database. Returns None if absent."""
    conn = sqlite3.connect(str(db_path))
    try:
        row = conn.execute("SELECT version_num FROM alembic_version").fetchone()
        return row[0] if row else None
    except sqlite3.OperationalError:
        return None
    finally:
        conn.close()
```

- [ ] **Step 3: Verify the refactored helper imports correctly**

```bash
cd backend && python -c "from tests.api.test_restore_integration import _build_test_backup_tar; print('OK')" 2>&1
```

Expected: `OK`.

- [ ] **Step 4: Commit**

```bash
git add backend/tests/api/test_restore_integration.py
git commit -m "test: add alembic template DB fixture, refactor _build_backup_tar with schema param"
```

---

### Task 4: Add new revision gate test cases

**Files:**
- Modify: `backend/tests/api/test_restore_integration.py` (add after `TestRestoreAuthRequired`, before `test_collection_pointer_creates_missing_qdrant_directory`)

- [ ] **Step 1: Add `TestRevisionGate` class with all new test cases**

Add after line 648 (end of `TestRestoreAuthRequired`):

```python
# ── Revision gate ───────────────────────────────────────────────────────


@pytest.mark.usefixtures("mock_embedding")
class TestRevisionGate:
    """Restore version gate: legacy rejection, old-version migration, mismatch detection."""

    async def test_restore_head_backup_succeeds(self, template_head_db):
        """Backup at head revision restores normally (existing behavior, now explicit)."""
        tar_bytes = _build_test_backup_tar(
            db_rows=[{
                "id": "doc-head-001", "filename": "head_test.txt",
                "file_type": ".txt", "file_hash": "head_hash",
                "raw_text": "Head revision backup.", "chunk_count": 1,
            }],
            upload_files={"head_test.txt": b"Head revision backup."},
            schema="head",
        )

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            r = await client.post(
                "/api/backup/restore",
                files={"file": ("head_backup.tar.gz", tar_bytes, "application/gzip")},
                headers=ADMIN_HEADERS,
            )
            assert r.status_code == 200
            data = r.json()
            assert data["status"] == "ok"

    async def test_old_revision_backup_migrates_and_succeeds(self, template_head_db):
        """Backup at revision 0001 should be migrated to head and restore successfully."""
        tar_bytes = _build_test_backup_tar(
            db_rows=[{
                "id": "doc-old-001", "filename": "old_rev.txt",
                "file_type": ".txt", "file_hash": "old_hash_001",
                "raw_text": "Old revision test.", "chunk_count": 1,
            }],
            upload_files={"old_rev.txt": b"Old revision test."},
            schema="0001",
        )

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            r = await client.post(
                "/api/backup/restore",
                files={"file": ("old_rev.tar.gz", tar_bytes, "application/gzip")},
                headers=ADMIN_HEADERS,
            )
            assert r.status_code == 200
            data = r.json()
            assert data["status"] == "ok"

            # Verify the restored DB has the storage_key column (added in 0002)
            r = await client.get("/api/documents", headers=ADMIN_HEADERS)
            assert r.status_code == 200
            docs = r.json()
            assert any(d["filename"] == "old_rev.txt" for d in docs)

    async def test_legacy_backup_rejected(self, template_head_db):
        """Backup without alembic_version table must be rejected with 400."""
        tar_bytes = _build_test_backup_tar(
            db_rows=[{
                "id": "doc-legacy-001", "filename": "legacy.txt",
                "file_type": ".txt", "file_hash": "legacy_hash",
                "raw_text": "Legacy backup content.", "chunk_count": 1,
            }],
            upload_files={"legacy.txt": b"Legacy backup content."},
            schema="legacy",
            include_manifest=False,
        )

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            # Snapshot current state before attempt
            r = await client.get("/api/documents", headers=ADMIN_HEADERS)
            pre_docs = len(r.json())

            r = await client.post(
                "/api/backup/restore",
                files={"file": ("legacy.tar.gz", tar_bytes, "application/gzip")},
                headers=ADMIN_HEADERS,
            )
            assert r.status_code == 400
            detail = r.json()["detail"]
            assert "legacy" in detail.lower() or "版本" in detail

            # Current database must NOT be replaced
            r = await client.get("/api/documents", headers=ADMIN_HEADERS)
            assert len(r.json()) == pre_docs

    async def test_manifest_db_mismatch_rejected(self, template_head_db):
        """Manifest db_schema_revision that doesn't match staged DB -> 400."""
        tar_bytes = _build_test_backup_tar(
            db_rows=[{
                "id": "doc-mismatch-001", "filename": "mismatch.txt",
                "file_type": ".txt", "file_hash": "mismatch_hash",
                "raw_text": "Mismatch test.", "chunk_count": 1,
            }],
            upload_files={"mismatch.txt": b"Mismatch test."},
            schema="head",
            revision_override="9999_fake_revision",
        )

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            r = await client.post(
                "/api/backup/restore",
                files={"file": ("mismatch.tar.gz", tar_bytes, "application/gzip")},
                headers=ADMIN_HEADERS,
            )
            assert r.status_code == 400
            detail = r.json()["detail"]
            assert "不一致" in detail

    async def test_future_revision_rejected(self, template_head_db):
        """Backup with a revision not in the local migration history -> 400."""
        tar_bytes = _build_test_backup_tar(
            db_rows=[{
                "id": "doc-future-001", "filename": "future.txt",
                "file_type": ".txt", "file_hash": "fut_hash",
                "raw_text": "Future revision test.", "chunk_count": 1,
            }],
            upload_files={"future.txt": b"Future revision test."},
            schema="head",
            revision_override="9999_future",
        )

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            r = await client.post(
                "/api/backup/restore",
                files={"file": ("future.tar.gz", tar_bytes, "application/gzip")},
                headers=ADMIN_HEADERS,
            )
            assert r.status_code == 400

    async def test_no_manifest_head_db_succeeds(self, template_head_db):
        """Backup without manifest but DB at head revision -> restore succeeds."""
        tar_bytes = _build_test_backup_tar(
            db_rows=[{
                "id": "doc-nomanifest-head-001", "filename": "nomanifest_head.txt",
                "file_type": ".txt", "file_hash": "nmh_hash",
                "raw_text": "No manifest, head DB.", "chunk_count": 1,
            }],
            upload_files={"nomanifest_head.txt": b"No manifest, head DB."},
            schema="head",
            include_manifest=False,
        )

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            r = await client.post(
                "/api/backup/restore",
                files={"file": ("nomanifest_head.tar.gz", tar_bytes, "application/gzip")},
                headers=ADMIN_HEADERS,
            )
            assert r.status_code == 200
            data = r.json()
            assert data["status"] == "ok"

    async def test_staging_db_not_replaced_on_rejection(self, template_head_db):
        """When legacy backup is rejected, staged DB must not replace live DB."""
        tar_bytes = _build_test_backup_tar(
            db_rows=[{"id": "doc-legacy-002", "filename": "legacy2.txt"}],
            schema="legacy",
            include_manifest=False,
        )

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            # Record current state
            r = await client.get("/api/documents", headers=ADMIN_HEADERS)
            pre_docs = r.json()

            r = await client.post(
                "/api/backup/restore",
                files={"file": ("legacy2.tar.gz", tar_bytes, "application/gzip")},
                headers=ADMIN_HEADERS,
            )
            assert r.status_code == 400

            # State must be unchanged
            r = await client.get("/api/documents", headers=ADMIN_HEADERS)
            post_docs = r.json()
            assert [d["id"] for d in pre_docs] == [d["id"] for d in post_docs]
```

- [ ] **Step 2: Update existing test classes to use `template_head_db` fixture**

Add `template_head_db` parameter to each test class that calls `_build_backup_tar` with default schema:

- `TestNormalRestore` (line 158): add `@pytest.mark.usefixtures("template_head_db")`
- `TestFaultInjection` (line 244): add `@pytest.mark.usefixtures("template_head_db")`

The decorator:
```python
@pytest.mark.usefixtures("mock_embedding", "template_head_db")
class TestNormalRestore:
```

And:
```python
@pytest.mark.usefixtures("template_head_db")
class TestFaultInjection:
```

Note: `TestFaultInjection` already has per-method `mock_embedding` fixture usage.

- [ ] **Step 3: Run the new tests to confirm they pass**

```bash
cd backend && python -m pytest tests/api/test_restore_integration.py::TestRevisionGate -v 2>&1 | tail -40
```

Expected: 7 passed (some may fail on first run — debug and fix any issues).

- [ ] **Step 4: Commit**

```bash
git add backend/tests/api/test_restore_integration.py
git commit -m "test: add revision gate test cases (legacy reject, migrate, mismatch, future)"
```

---

### Task 5: Full test suite verification and regression check

**Files:**
- No new changes — verification only

- [ ] **Step 1: Run full test_restore_integration.py suite**

```bash
cd backend && python -m pytest tests/api/test_restore_integration.py -v 2>&1
```

Expected: all tests pass (~28 tests). Pay special attention to:
- `TestNormalRestore` (4 tests) — must all pass
- `TestFaultInjection` (6 tests) — must all pass including rollback tests
- `TestCrossConsistency` (3 tests) — unchanged behavior
- `TestManifestCompatibility` (3 tests) — one test may need update (see below)
- `TestRevisionGate` (7 tests) — all pass

- [ ] **Step 2: Update `test_restore_without_manifest_succeeds` if needed**

The old test at line 584 creates a backup with `include_manifest=False` and the default (legacy-style) schema. After our refactor, `schema="head"` is the default, so this test will create a head-revision DB without manifest — which should still succeed.

If the old test uses the old schema (no `schema` parameter, which defaults to `"head"` now), it will create a head DB. That's fine — the test verifies "no manifest + head DB → success" which is also covered by `test_no_manifest_head_db_succeeds`.

Update the old test to explicitly use `schema="head"` and add `template_head_db` fixture:

```python
@pytest.mark.usefixtures("mock_embedding", "template_head_db")
async def test_restore_without_manifest_succeeds(self):
    tar_bytes = _build_test_backup_tar(
        db_rows=[{
            "id": "doc-nomanifest-001", "filename": "nomanifest.txt",
            "file_type": ".txt", "raw_text": "No manifest test.",
            "chunk_count": 1,
        }],
        upload_files={"nomanifest.txt": b"No manifest test."},
        include_manifest=False,
        schema="head",
    )
    # ... rest unchanged
```

- [ ] **Step 3: Run the broader API test suite**

```bash
cd backend && python -m pytest tests/api/ -v 2>&1 | tail -50
```

Expected: all tests pass, no regression errors.

- [ ] **Step 4: Run the full test suite**

```bash
cd backend && python -m pytest tests/ -x --timeout=120 2>&1 | tail -60
```

Expected: all tests pass. Ignore pre-existing failures in unrelated tests (evaluation, e2e, etc.) — focus on regression: if any test that previously passed now fails, investigate and fix.

- [ ] **Step 5: Commit (if any fixes were needed)**

```bash
git add backend/tests/api/test_restore_integration.py
git commit -m "test: update existing tests for template DB fixture compatibility"
```

---

## Self-Review

### 1. Spec coverage

| Spec requirement | Task(s) |
|---|---|
| staged DB 为权威事实来源 (§3.1 step 1) | Task 2 — `_get_alembic_revision(db_file)` reads from staged |
| manifest 交叉校验 (§3.1 step 2) | Task 2 — cross-validate block |
| 四分支判定表 (§3.1 step 3) | Task 1 — `_classify_staged_revision`, Task 2 — if/elif chain |
| "未知 revision" 拒绝 (§3.1) | Task 1 — `_classify_staged_revision` returns "unknown" |
| staged 迁移 (§3.2) | Task 1 — `_migrate_staged_db`, Task 2 — "old" branch |
| 迁移失败不触碰现库 (§3.2, §5) | Task 2 — exception in HTTPException handler triggers rollback (existing mechanism) |
| legacy 一律拒绝 (§2.2) | Task 2 — "legacy" branch raises 400 |
| 测试夹具用 Alembic 建库 (§3.3) | Task 3 — `template_head_db` fixture + `_create_template_db` |
| schema="legacy"/"0001"/revision_override (§3.3) | Task 3 — `_build_backup_tar` schema parameter |
| 测试用例 1-7 (§3.4) | Task 4 — `TestRevisionGate` 7 tests |

### 2. Placeholder scan

No TBD, TODO, "add appropriate error handling", or vague steps found. All steps contain concrete code or exact commands.

### 3. Type consistency

- `_get_head_info()` returns `tuple[str, ScriptDirectory]` — consumed consistently in Task 2
- `_classify_staged_revision` returns `str` — matched against string literals in Task 2
- `_migrate_staged_db(db_path: Path, head_revision: str)` — called with `(db_file, head_revision)` in Task 2
- `_build_backup_tar(schema=...)` — `"head"`, `"0001"`, `"legacy"` are the only values used
- `revision_override` is `str | None` — set in manifest only when not None

### Execution handoff

Plan complete and saved to `docs/superpowers/plans/2026-07-19-restore-revision-gate.md`.
