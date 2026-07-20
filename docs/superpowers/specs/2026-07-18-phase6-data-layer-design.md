# Phase 6：数据层工程化设计

> 状态：已 Review，可进入实现计划阶段
>
> 日期：2026-07-18
>
> 范围：迁移体系、数据库访问边界、文件存储边界、备份恢复兼容性
>
> 决策：本阶段继续正式支持 SQLite；建立可演进的数据层边界，但不宣称已经支持 PostgreSQL 或多实例写入

## 1. 背景与结论

当前数据层可以满足单机运行，但数据库初始化、运行时补表、SQLite 专用 SQL、会话创建、文件路径和备份恢复逻辑分散在多个模块中。继续直接叠加功能会带来以下风险：

- 已有数据库与新安装数据库的结构可能不同；
- 应用启动时执行 DDL，在多进程或多副本下可能发生竞争；
- `PRAGMA`、FTS5、BM25 和 SQLite 时间函数散落，未来无法安全切换数据库；
- 文件记录只依赖本地路径，难以验证、迁移和恢复；
- 备份缺少可信的数据库迁移版本，旧备份恢复后可能静默漂移；
- 会话提交、回滚和关闭的所有权不统一，异常路径容易留下部分写入。

本阶段采用“先收敛边界、再扩展数据库”的策略：

1. Alembic 成为唯一的数据库结构版本管理入口；
2. 生产启动只校验迁移版本，不隐式建表或升级；
3. 显式支持 SQLite 能力，不用未实现的 PostgreSQL 分支制造兼容假象；
4. 统一会话生命周期和事务语义；
5. 引入支持流式 I/O、临时暂存和原子提交的文件存储接口；
6. 让备份、恢复和数据库迁移使用同一套版本事实来源。

## 2. 目标与非目标

### 2.1 目标

- 新数据库能够从空库执行 `alembic upgrade head` 后完整运行；
- 现有 SQLite 数据库能够经过结构指纹校验后安全纳入 Alembic 管理；
- 应用启动时能够发现“数据库版本落后、超前或未知”，并明确失败；
- 所有数据库会话具有清晰的提交、回滚、关闭责任；
- 文件上传、解析、重建、删除、备份和恢复通过统一存储边界访问文件；
- 备份清单记录 Alembic revision，并按版本规则执行恢复；
- 保持现有 SQLite 功能、数据和 API 行为不回退。

### 2.2 非目标

- 本阶段不实现 PostgreSQL 驱动、`tsvector` 检索或 PostgreSQL 运维；
- 不实现 S3、MinIO、OSS 等对象存储后端；
- 不承诺 SQLite 多副本并发写入；
- 不重写 RAG 检索算法、分块算法或索引排序逻辑；
- 不在生产数据库上依赖破坏性 `alembic downgrade` 作为回滚手段。

### 2.3 必须保持的不变量

- 升级前后文档、分块、会话、消息、用户、任务、索引代次和审计数据数量一致；
- FTS5 与 BM25 索引能够重建，且活动索引代次保持一致；
- 文件不得因同名覆盖、路径穿越或中途失败而损坏；
- 迁移、恢复和索引重建不得并发执行；
- 未知数据库结构不得被盲目 `stamp` 为当前版本。

## 3. 当前结构盘点与实施前置门槛

实施前先生成一份可复核的数据库结构清单，至少覆盖：

- SQLAlchemy ORM 表及列、约束、索引；
- `init_db()` 中通过原始 SQL 创建或修改的表；
- `documents`、`chunks`、`conversations`、`messages`、用户与记忆相关表；
- `index_generations`、`task_queue`、`audit_logs` 等非完整 ORM 管理表；
- FTS5 虚拟表、触发器或同步规则；
- BM25 文档表、词项表及相关索引；
- 当前使用的 `PRAGMA`、`ALTER TABLE`、`INSERT OR REPLACE`、`datetime('now')` 等 SQLite 专用语句。

结构清单是基线迁移和旧库指纹校验的依据。若 ORM 定义与生产结构存在差异，先明确“生产真实结构”，不得直接相信 `Base.metadata` 或 Alembic autogenerate 的单一结果。

## 4. Alembic 迁移设计

### 4.1 迁移文件

建议建立以下初始修订：

```text
0001_current_sqlite_schema
  └─ 当前受支持 SQLite 完整结构的权威基线
     包含普通表、约束、索引、FTS5、BM25 及必要的初始化数据

0002_document_storage_key
  └─ 为 documents 增加 nullable storage_key
     执行旧文件定位和回填，记录无法唯一匹配的异常
```

`0001` 必须人工审阅和补全，不能只提交 autogenerate 结果。Alembic autogenerate 无法可靠表达 FTS5 虚拟表、部分原始 SQL 表以及 SQLite 特殊索引。

从本阶段开始：

- 所有结构 DDL 只存在于 Alembic migration；
- `init_db()` 不再补列、建表、删表、重建 FTS 或 BM25 表；
- tokenizer 或 FTS 结构变化必须作为有版本的数据迁移；
- 每个迁移都注明是否可逆、数据量风险及预计锁定时间。

### 4.2 空数据库初始化

空数据库只通过以下方式创建：

```powershell
alembic upgrade head
```

执行完成后，应用启动校验数据库 revision 等于代码的 Alembic head。不得再调用 `Base.metadata.create_all()` 兜底，因为兜底会掩盖缺失迁移并产生不可追踪结构。

### 4.3 现有数据库纳管

现有数据库不能直接运行创建全部表的 `0001`，也不能无条件 `alembic stamp head`。采用以下流程：

1. 停止写入并进入维护模式；
2. 生成 SQLite 文件备份和 SHA-256；
3. 运行结构指纹检查，比较表、列、索引和关键虚拟表；
4. 若结构与 `0001` 完全一致，执行 `alembic stamp 0001_current_sqlite_schema`；
5. 再执行 `alembic upgrade head`，应用 `0002` 及后续迁移；
6. 运行数据一致性、文件映射和索引健康检查；
7. 退出维护模式。

若指纹显示数据库更旧或存在漂移：

- 不允许盲目 stamp；
- 输出具体缺失表、列、索引和冲突项；
- 为已知旧版本提供显式的“旧版修复迁移 → 基线纳管”路径；
- 未知漂移必须先人工确认并备份，再生成针对性修复方案。

### 4.4 迁移执行策略

生产环境中，迁移是独立部署步骤：

```text
停止写入/维护模式
  → 备份与校验
  → 单实例执行 alembic upgrade head
  → 数据与 revision 验证
  → 启动应用
```

应用启动仅执行 revision gate：

- current == head：允许启动；
- current 落后于 head：拒绝启动，提示执行迁移；
- current 超前于 head：拒绝启动，提示代码版本过旧；
- 无 revision 且非空库：拒绝启动，提示执行旧库纳管；
- 空库：拒绝生产启动，提示先执行迁移。

可选的 `AUTO_MIGRATE=true` 仅允许本地开发或测试环境使用，并限制为 SQLite 单进程。生产配置发现该选项时应告警或拒绝。

## 5. SQLite 运行时配置

SQLite 配置分为连接级和数据库级，不应在每次业务初始化中混合执行。

### 5.1 连接级配置

通过 SQLAlchemy `engine.sync_engine` 的 connect event，为每个新连接设置：

```sql
PRAGMA foreign_keys = ON;
PRAGMA busy_timeout = <configured_ms>;
```

这是必要的，因为 `foreign_keys` 和 `busy_timeout` 是连接级行为，只在一次启动 SQL 中设置不能覆盖连接池后续创建的连接。

### 5.2 数据库级配置

`journal_mode=WAL` 在迁移或显式 bootstrap 阶段设置并验证，不放进普通请求路径。若运行环境的文件系统不支持 WAL，启动健康检查必须返回清晰错误，而不是静默降级。

## 6. 数据库能力边界

建立显式的 capability/adapter 层，集中管理数据库差异，但本阶段只实现 SQLite：

```python
class DatabaseCapabilities(Protocol):
    dialect_name: str
    supports_fts: bool
    supports_atomic_file_db_switch: bool

    async def health_check(self, session: AsyncSession) -> None: ...
    async def rebuild_fts(self, session: AsyncSession) -> None: ...
```

原则：

- SQLite 适配器负责已验证的 SQLite 查询行为；
- 未支持的 dialect 立即抛出 `UnsupportedDialectError`；
- adapter 不在运行时创建或修改 schema；
- PostgreSQL 适配器只有真正实现并通过测试后才加入；
- 将 `PRAGMA`、FTS5、`INSERT OR REPLACE`、SQLite 时间表达式等方言语句集中盘点和隔离；
- 通用 ORM 查询保持在 repository/service 层，不为抽象而抽象。

本设计提供未来迁移入口，但不等于完成 PostgreSQL 兼容。

## 7. 会话与事务生命周期

提供两个明确入口：

```python
def new_session() -> AsyncSession:
    """创建独立会话；调用方负责 commit/rollback/close。"""

@asynccontextmanager
async def session_scope() -> AsyncIterator[AsyncSession]:
    """统一关闭；异常时 rollback；不隐式 commit。"""
```

API 依赖 `get_db()` 委托给上述实现，但仍由 endpoint/service 明确决定何时提交。禁止 context manager 在成功退出时自动提交，因为这会隐藏事务边界，并可能把读取流程中的意外修改提交到数据库。

迁移现有调用点时按用途分类：

- 纯读取：使用 `session_scope()`，不提交；
- 单事务写入：service 明确 `commit()`，失败统一 rollback；
- 后台任务：每个任务或可恢复批次持有独立短会话；
- 流式响应：数据库查询完成后尽早释放会话，不跨整个模型流式输出持有事务；
- 多阶段任务：不得用一个长事务覆盖文件解析、模型调用和索引构建。

为降低一次性改造风险，可保留原 `async_session` 名称作为一个发布周期的兼容别名，并加入弃用告警；但禁止新增直接调用。

## 8. 文件存储边界

### 8.1 接口要求

文件可能达到数十至数百 MB，接口必须支持流式读写，不能通过单个 `bytes` 将完整文件载入内存。建议接口：

```python
class FileStorage(Protocol):
    async def create_staging(self, filename: str) -> StagedObject: ...
    async def append(self, staged: StagedObject, chunk: bytes) -> None: ...
    async def commit(self, staged: StagedObject, *, expected_sha256: str | None = None) -> StoredObject: ...
    async def abort(self, staged: StagedObject) -> None: ...
    async def open_read(self, storage_key: str) -> AsyncContextManager[AsyncReadable]: ...
    async def delete(self, storage_key: str) -> None: ...
    async def exists(self, storage_key: str) -> bool: ...
```

`LocalFileStorage` 必须保留现有可靠语义：

- 上传先写入受控临时目录；
- 写入过程中计算大小和 SHA-256；
- 校验成功后原子移动到最终位置；
- 同名文件使用无冲突 storage key，不覆盖已有文件；
- 所有路径规范化并验证仍位于配置根目录下，阻断 `..`、绝对路径和符号链接逃逸；
- 中断、超时或失败后清理临时文件；
- 删除操作幂等，并区分“记录不存在”和“物理文件已丢失”的审计状态。

对象存储未来实现时不能假设存在原子 rename，应通过临时对象、复制/提交标记和最终一致性设计实现等价语义。

### 8.2 数据库字段

为 `documents` 增加：

```text
storage_key  nullable string, indexed as needed
```

`storage_key` 是存储系统内部标识，不等同于用户文件名或任意绝对路径。原始文件名继续作为展示元数据保存。

旧数据回填流程：

1. 根据当前路径规则查找候选文件；
2. 校验大小，已有 hash 时同时校验 hash；
3. 唯一匹配时写入 storage key；
4. 无匹配或多匹配时保持 null，记录可操作的异常报告；
5. 未完成回填的文档不得被静默视为文件可用。

待所有受支持数据库完成回填并经过至少一个稳定发布周期后，再评估将字段改为非空；本阶段不强制 `NOT NULL`。

### 8.3 调用链改造

仅让新接口包装旧函数而不迁移调用点，不构成有效抽象。本阶段至少将以下链路改为使用 storage service：

- 上传和批量上传；
- 文档解析与重新处理；
- 文档删除与知识库清空；
- 索引重建需要读取源文件的路径；
- 备份文件收集与恢复文件落盘；
- 文件存在性与健康检查。

兼容函数可以临时委托给 storage singleton，但需要标记弃用并禁止新代码直接拼接本地文件路径。

## 9. 备份与恢复

备份 manifest 增加并校验：

```json
{
  "format_version": 2,
  "app_version": "...",
  "db_schema_revision": "<alembic revision>",
  "created_at": "...",
  "database_sha256": "..."
}
```

恢复规则：

- 备份 revision == 当前 head：在 staging 区校验后恢复；
- 备份 revision 早于当前 head：先在 staging 数据库执行迁移，再做一致性检查和切换；
- 备份 revision 晚于当前 head：拒绝恢复，要求使用更新版本应用；
- 旧格式备份没有 revision：仅允许通过已知结构指纹识别，不允许自动猜测；
- 数据库与文件清单、hash、文档 storage key 必须一致；
- 任何校验失败都不得替换当前有效数据。

恢复、迁移、索引重建和清空知识库共用维护锁。恢复使用 staged database/staged files，全部校验成功后再切换；切换失败必须能够保留当前有效版本。

## 10. 实施分阶段

### 阶段 A：结构盘点与安全网

- 输出当前权威 schema inventory 和 SQLite 专用 SQL 清单；
- 增加数据库指纹工具；
- 增加迁移前备份、hash 和维护锁；
- 固化现有数据一致性测试。

### 阶段 B：Alembic 基线

- 接入 Alembic async 配置；
- 编写并人工核对 `0001_current_sqlite_schema`；
- 编写空库初始化、旧库纳管、漂移拒绝测试；
- 从 `init_db()` 移除运行时 DDL；
- 增加启动 revision gate 和部署迁移命令。

### 阶段 C：连接与会话收敛

- 通过连接事件设置 SQLite connection PRAGMA；
- 增加 SQLite capability adapter 和不支持方言的明确错误；
- 引入 `new_session()`、`session_scope()`；
- 按读取、写入、后台任务、流式响应分类迁移调用点；
- 为事务回滚和资源释放增加测试。

### 阶段 D：文件存储收敛

- 实现流式 `LocalFileStorage`；
- 添加 `0002_document_storage_key`；
- 执行旧文件回填和异常报告；
- 改造上传、处理、删除、重建、备份和恢复调用链；
- 增加路径安全、原子提交、冲突和失败清理测试。

### 阶段 E：备份恢复与部署验收

- manifest 写入 Alembic revision；
- 实现 staging 数据库迁移和版本拒绝规则；
- 完成全量后端、前端和 Docker E2E；
- 在生产数据副本上完成升级与恢复演练；
- 记录耗时、磁盘放大和停机窗口。

## 11. 测试矩阵

### 11.1 数据库迁移

- 空 SQLite 数据库 upgrade 到 head；
- 与 `0001` 完全一致的旧库能够校验并 stamp；
- 缺列、多列、错误索引、缺失 FTS/BM25 的数据库被拒绝纳管；
- 已知旧版数据库经过显式迁移后纳管；
- `0001 → head` 后所有业务数据和索引代次保持一致；
- schema inventory 覆盖 ORM 表、原始 SQL 表、虚拟表和索引；
- 应用对落后、超前、无 revision 非空库正确拒绝启动；
- downgrade 仅在一次性测试数据库验证迁移声明，不作为生产回滚验收。

### 11.2 会话与事务

- 成功写入只在显式 commit 后可见；
- 异常自动 rollback；
- 读取不产生隐式 commit；
- 并发后台任务使用独立会话；
- 流式模型响应期间不长期占用数据库事务；
- 会话在取消、异常和超时路径都能关闭。

### 11.3 文件存储

- 大文件使用固定大小 chunk 流式写入和读取；
- 同名文件不覆盖；
- hash 或大小不一致时不提交；
- 上传中断后临时文件被清理；
- 路径穿越、绝对路径、符号链接逃逸被拒绝；
- 删除幂等且状态可审计；
- storage key 回填对唯一、缺失、多匹配场景输出正确结果；
- 上传、重新处理、删除、清空和重建不直接依赖绝对路径。

### 11.4 备份恢复

- manifest 包含真实 Alembic revision；
- 当前版本备份可恢复；
- 旧 revision 在 staging 中迁移后可恢复；
- 新 revision 备份被旧应用拒绝；
- 无 revision 的遗留备份必须通过指纹校验；
- 数据库、文件和 hash 不一致时禁止切换；
- 恢复失败后当前实例仍可用。

### 11.5 回归与部署

- 运行完整后端测试集，不以固定的“60 个测试”子集作为通过标准；
- 运行前端测试与生产构建；
- 运行真实 Docker 全链路验收：迁移、启动、上传、检索、删除、备份、恢复、重启；
- 在生产规模数据副本上记录迁移时间、恢复时间、峰值内存和额外磁盘占用。

## 12. 发布与回滚

### 12.1 发布流程

1. 在生产数据副本完成一次升级和恢复演练；
2. 发布前进入维护模式并停止写入；
3. 检查可用磁盘空间；
4. 生成数据库及文件备份并记录 SHA-256；
5. 运行结构指纹检查；
6. 首次纳管数据库时 stamp `0001`；
7. 单实例执行 `alembic upgrade head`；
8. 执行 revision、数据、文件和索引一致性检查；
9. 启动应用并完成健康检查和核心 smoke test；
10. 恢复流量，持续观察错误率、数据库锁等待和任务积压。

### 12.2 回滚策略

生产回滚采用“旧应用镜像 + 迁移前数据库/文件备份恢复”，不依赖 Alembic downgrade。原因是删列、数据重写、FTS 重建和 storage key 回填通常无法保证无损逆转。

触发回滚时：

1. 再次停止写入；
2. 保存故障现场副本用于排查；
3. 恢复迁移前数据库和对应文件快照；
4. 校验 hash 和索引一致性；
5. 启动旧应用版本并执行 smoke test；
6. 恢复流量。

## 13. 风险与缓解

| 风险 | 影响 | 缓解措施 |
| --- | --- | --- |
| 现有数据库结构漂移 | stamp 后后续迁移失败或数据损坏 | 权威 inventory、严格指纹、禁止盲 stamp |
| 启动时并发迁移 | 多副本 DDL 竞争 | 迁移独立执行，应用只做 revision gate |
| SQLite 连接 PRAGMA 未覆盖连接池 | 外键或锁等待行为不一致 | connect event 对每个连接设置 |
| 大文件全量载入内存 | OOM、长时间阻塞 | 分块流式 I/O、大小和 hash 在线校验 |
| storage key 回填错误 | 文件错配或不可删除 | 唯一匹配、hash 校验、异常报告、保持 nullable |
| 恢复旧备份后 schema 不兼容 | 启动失败或静默错误 | staging migration、revision 规则、切换前检查 |
| 将 adapter 误认为已支持 PostgreSQL | 部署后才暴露方言错误 | 未支持 dialect 立即失败，单独立项实现 PG |
| 迁移时间超过维护窗口 | 服务长时间不可用 | 生产副本演练、计时、磁盘容量预检、回滚点 |

## 14. 验收标准

本阶段只有同时满足以下条件才视为完成：

- [ ] Alembic 是唯一 schema 变更入口，运行时不再执行建表或补列 DDL；
- [ ] 空库可升级到 head，已有受支持数据库可安全纳管；
- [ ] 未知漂移数据库会失败并输出可操作诊断；
- [ ] 生产应用启动只校验 revision，不自动迁移和 `create_all`；
- [ ] SQLite 每个连接正确设置外键和 busy timeout；
- [ ] 不支持的数据库方言明确失败，不静默运行；
- [ ] 会话提交、回滚、关闭责任清晰且测试覆盖异常路径；
- [ ] 大文件上传和读取为流式处理，保留原子提交及路径安全；
- [ ] 文档具有稳定 storage key，旧数据回填异常可追踪；
- [ ] 上传、处理、删除、重建、备份、恢复均通过存储边界；
- [ ] 备份记录 Alembic revision，旧版/新版/遗留备份规则通过测试；
- [ ] 完整后端、前端构建和 Docker E2E 通过；
- [ ] 已在生产数据副本完成升级和恢复演练并记录结果。

## 15. 工作量判断

该改造不是“约 250 行且不触碰业务逻辑”的机械重构。文件存储边界会影响上传、解析、删除、重建、备份和恢复；数据库迁移还需要覆盖当前由原始 SQL 管理的结构。

预计为中高风险改造，代码和迁移测试总变更量大致在 **500–900 行以上**，实际取决于结构漂移数量、旧文件命名情况和现有测试可复用程度。建议按上述阶段拆分提交，每个阶段均保持可验证、可回滚，不在一个提交中同时替换全部数据层。

## 16. 后续阶段

完成本阶段并稳定运行后，再单独设计 PostgreSQL 迁移，至少包括：

- PostgreSQL schema migration；
- FTS5/BM25 的 PostgreSQL 等价实现或外部检索方案；
- SQLite 专用 SQL 替换；
- 双数据库契约测试；
- 数据搬迁、校验、双写或停机切换策略；
- 多实例任务抢占、分布式锁和对象存储。

这些能力未完成前，部署约束应继续明确为：**SQLite 单实例写入、本地持久卷、迁移单实例执行**。
