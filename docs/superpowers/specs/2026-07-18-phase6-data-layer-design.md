# Phase 6: Data Layer Abstraction & Migration Infrastructure Design

> 日期：2026-07-18
> 决策：Option B-steady — 引入 Alembic + DB 抽象层，保持 SQLite，为将来切 PostgreSQL 预留接口
> 基线：Phase 0–5 完成，60/60 测试通过

## 1. 范围与目标

### 做什么

1. **Alembic 迁移体系** — 将散落在 `init_db()` 中的 ad-hoc DDL（ALTER TABLE、CREATE TABLE IF NOT EXISTS）迁移为版本化 migration 脚本
2. **会话管理统一** — 收拢 20+ 文件中 `async_session` 的直接引用，建立单一入口 `get_session()`
3. **方言适配层** — 将 SQLite 特有的 PRAGMA、FTS5 虚拟表、BM25 原始 SQL 隔离到 `DialectAdapter`，PG 侧留接口不实现
4. **文件存储接口** — `LocalFileStorage` 实现 `FileStorage` 抽象基类，为对象存储留接口

### 不做什么

- 不安装 PostgreSQL / asyncpg
- 不引入 Repository 模式（SQLAlchemy 本身就是抽象层）
- 不动 Qdrant / 向量存储
- 不引入 Redis
- 不实现 PG 版 BM25 全文搜索（仅留接口）

## 2. Alembic 迁移体系

### 目录结构

```
backend/
  alembic.ini                    # 从 .env 读取 DATABASE_URL
  alembic/
    env.py                       # 异步引擎配置
    script.py.mako               # migration 模板
    versions/
      001_initial_schema.py      # 从 ORM 模型 autogenerate
      002_audit_logs.py          # audit_logs 表（原 init_db 中 CREATE TABLE IF NOT EXISTS）
      003_users_table.py         # users 表（原 init_db 中 CREATE TABLE IF NOT EXISTS）
      ...
```

### init_db() 瘦身

**保留（运行时特性，非 schema）：**
- PRAGMA 设置（WAL / busy_timeout / foreign_keys）→ 移至 DialectAdapter
- FTS5 虚拟表创建 → 移至 DialectAdapter
- BM25 倒排索引表创建 → 移至 DialectAdapter

**移除（交给 Alembic）：**
- 所有 `ALTER TABLE ADD COLUMN`
- 所有 `CREATE TABLE IF NOT EXISTS`
- 所有 `PRAGMA table_info()` 列存在性检查

**新增：**
- `alembic upgrade head` 在 `Base.metadata.create_all` 之前运行
- `create_all` 仅作为 fallback（migration 已覆盖所有表时为空操作）

### 迁移脚本生成策略

- `001` — `alembic revision --autogenerate -m "initial_schema"` 自动生成
- `002+` — 手工编写，将 `init_db()` 中剩余的 DDL 逐条迁移
- 每个 migration 必须包含 `upgrade()` 和 `downgrade()`

## 3. 会话管理统一

### 当前问题

20+ 文件各自 `from models.database import async_session` 并直接 `async with async_session() as session:`。未来切 PG 需要改连接参数时，所有文件都要感知。

### 方案

```python
# models/database.py

def get_session(**overrides) -> AsyncSession:
    """返回一个新的异步会话。所有数据库访问必须通过此函数。"""
    return async_session(**overrides)

async def get_db() -> AsyncSession:
    """FastAPI 依赖注入入口。"""
    async with get_session() as session:
        yield session
```

### 替换策略（机械替换，不改变业务逻辑）

| 模式 | 替换 |
|---|---|
| `from models.database import async_session` | `from models.database import get_session` |
| `async with async_session() as session:` | `async with get_session() as session:` |
| `Depends(get_db)` | 不改动 |

涉及文件：`api/auth.py`, `api/users.py`, `api/audit.py`, `api/memories.py`, `api/chat.py`, `api/settings.py`, `main.py`, `memory/profile.py`, `rag/pipeline.py`, `rag/retriever.py`, `textdb/bm25_search.py`, `worker/tasks.py`, `agent/tools.py` 等约 15 个文件。

## 4. 方言适配层

### 问题

SQLite 特性在 PG 下无对应物：

| SQLite 特性 | PG 替代 |
|---|---|
| `PRAGMA journal_mode=WAL` | 不需要 |
| `CREATE VIRTUAL TABLE ... USING fts5` | `tsvector` 列 + GIN 索引 |
| BM25 自定义虚拟表 | `tsvector` / `tsquery` |
| `PRAGMA table_info('t')` | `information_schema.columns` |
| `MATCH` / `highlight()` | `to_tsvector` / `ts_headline` |

### DialectAdapter 接口

```python
# models/dialect.py

class DialectAdapter:
    def __init__(self, engine): ...
    @property
    def is_sqlite(self) -> bool: ...
    def get_pragmas(self) -> list[str]: ...
    def get_table_info_sql(self, table_name: str) -> str: ...
    def create_fts_tables(self, conn) -> None: ...
    def create_bm25_tables(self, conn) -> None: ...
```

- `is_sqlite=True` 时：执行原有 SQLite PRAGMA + FTS5 + BM25 DDL
- `is_sqlite=False` 时：PRAGMA 返回空、FTS5 替换为 `tsvector` 列（暂不实现具体逻辑）

### 使用方式

```python
# database.py init_db()
adapter = DialectAdapter(engine)
for pragma in adapter.get_pragmas():
    await conn.execute(text(pragma))
adapter.create_fts_tables(conn)
adapter.create_bm25_tables(conn)
```

## 5. 文件存储抽象

### 接口

```python
# storage/base.py

class FileStorage(ABC):
    @abstractmethod
    async def save(self, filename: str, content: bytes) -> str: ...
    @abstractmethod
    async def read(self, filepath: str) -> bytes: ...
    @abstractmethod
    async def delete(self, filepath: str) -> bool: ...
    @abstractmethod
    async def exists(self, filepath: str) -> bool: ...
```

### 现有实现

`storage/files.py` 中的 `save_upload()` / `find_upload()` / `delete_file()` 函数保持不变，`LocalFileStorage` 类实现 `FileStorage` 接口，内部委托给现有函数。

不影响任何调用方 — `storage/files.py` 导出的函数签名不变。

## 6. 测试与验证

### 新增测试

| 测试 | 覆盖 |
|---|---|
| `tests/test_alembic.py` | `upgrade → downgrade → upgrade` 循环，schema diff 一致性 |
| `tests/test_dialect.py` | `DialectAdapter` SQLite 路径全覆盖，PG 路径 smoke test |

### 回归验证

- 60/60 现有测试全部通过
- Docker smoke 5/5 通过
- 空数据库 `alembic upgrade head` 后 schema 与 `create_all` 一致

## 7. 改动量汇总

| 类别 | 改动量 | 风险 |
|---|---|---|
| Alembic 初始化 + env.py | ~80 行新文件 | 低 |
| Migration 脚本 | 1 个 auto + 3-5 个手工 | 中（需仔细核对 schema） |
| `init_db()` 瘦身 | 删 ~40 行，保留功能移至 dialect | 低 |
| `dialect.py` 新增 | ~60 行 | 低 |
| 会话统一（~15 文件） | 每文件 1-3 行机械替换 | 低 |
| `storage/base.py` 接口 | ~25 行 | 低 |
| 测试 | ~50 行 | 低 |
| `requirements.txt` | +alembic | 低 |

总计约 250 行新代码 + 15 个文件机械替换，零业务逻辑变更。
