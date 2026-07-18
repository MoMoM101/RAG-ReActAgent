# 恢复链路版本门禁与 staged 迁移设计

> 状态：已 Review，可进入实现计划阶段
>
> 日期：2026-07-18
>
> 范围：备份恢复的 schema 版本判定、staged 数据库迁移、restore 集成测试夹具重建
>
> 决策：恢复版本以 staged 数据库本体为权威事实来源；无版本标记的 legacy 备份一律拒绝（收窄
> Phase 6 spec 的"指纹识别"条款，见 §2.2）；旧版本备份在 staging 区迁移后再切换

## 1. 背景与问题

Phase 6 数据层设计（`2026-07-18-phase6-data-layer-design.md` §恢复规则）规定了按 Alembic
revision 执行恢复的四条规则，但现实现（`api/backup.py`）只落实了其中一条：

1. `_validate_restore_revision`（backup.py:138）只拒绝"备份版本晚于当前 head"；对
   旧版本与无 revision 的备份**直接放行原样恢复**，造成静默 schema 漂移——恢复完成后
   应用在缺列（如 `documents.storage_key`）、缺表（`users`、`audit_logs`）的库上运行，
   直到某次查询才炸。
2. 版本校验**只在 manifest 存在时执行**（backup.py:835）；无 manifest 的备份完全绕过
   版本校验。
3. `tests/api/test_restore_integration.py` 的 `_build_backup_tar` 夹具手写迁移前旧
   schema（无 `alembic_version`、无 `storage_key`/`users`/`audit_logs`），恢复后将测
   试数据库替换为旧 schema，导致同批次后续所有测试 setup 连锁失败（实测：1 failed 引
   发 24 errors）。

## 2. 目标与非目标

### 2.1 目标

- 恢复流程对任何备份都有明确的版本判定，杜绝静默 schema 漂移；
- 版本事实以 staged 数据库的 `alembic_version` 为权威，manifest 仅作交叉校验；
- 旧版本备份在 staging 区完成 `alembic upgrade head` 并复核后才允许切换；
- 任何校验或迁移失败都不得替换当前有效数据（复用现有 staged/回滚机制）；
- restore 集成测试夹具使用真实迁移建库，测试之间不再互相污染。

### 2.2 对 Phase 6 spec 的修订

Phase 6 spec 规定"旧格式备份没有 revision：仅允许通过已知结构指纹识别"。本设计将其
**收窄为一律拒绝**：现存唯一 legacy 备份已被放弃恢复，且其 schema 与 0001 基线不一致
（缺表），指纹注册表的维护成本没有对应收益。被拒绝的 legacy 备份由错误信息指引走离线
采纳流程（备份 → 指纹核对 → stamp → upgrade，见 `models/database.py` 启动 gate 的
adoption 提示）。

### 2.3 非目标

- 不实现 legacy 指纹注册表与自动采纳；
- 不修改备份创建侧（manifest `db_schema_revision` 自 format_version 2 起已写入）；
- 不实现 `alembic downgrade`；
- 不改动恢复流程的其他环节（uploads 原子切换、Qdrant 临时集合、回滚逻辑）。

## 3. 设计

### 3.1 版本判定（staged DB 为权威）

在 staged DB 完整性检查（`PRAGMA integrity_check`）之后、读取业务数据之前：

1. 读 staged DB 的 `alembic_version.version_num` 作为 `staged_revision`
   （表不存在或无行 → `None`）；
2. manifest 存在且含 `db_schema_revision` 时，须与 `staged_revision` 一致，
   不一致 → 400"备份 manifest 与数据库版本不一致"；
3. 按 `staged_revision` 与当前 head 分支：

| `staged_revision` | 处理 |
|---|---|
| `None`（legacy） | 400 拒绝，提示离线采纳流程 |
| == head | 继续现行流程 |
| < head | staged DB 上执行 `alembic upgrade head`（§3.2），复核 == head 后继续 |
| > head 或未知 revision | 400 拒绝，提示升级应用版本 |

"未知 revision"（不在本地迁移历史中的字符串）归入拒绝分支。现有
`_validate_restore_revision` 的数字前缀比较逻辑被替换为基于 Alembic
`ScriptDirectory` 迁移历史的判定：revision 在 `walk_revisions` 序列中的位置决定
新旧，不在序列中即未知。

### 3.2 staged 迁移

- 对 `restore_dir/rag_agent.db` 构造 AlembicConfig（`sqlalchemy.url` 指向 staged
  文件），执行 `alembic upgrade head`；Alembic API 为同步调用，包在
  `asyncio.to_thread` 中执行；
- 迁移在 restore 临时目录内进行，天然不触碰当前有效数据；
- 迁移抛出任何异常 → 400"备份数据库迁移失败"，restore 中止，现库不变；
- 迁移成功后重读 `alembic_version` 复核 == head，不一致视为迁移失败。

注：migration 0002 的 storage_key 回填按 `UPLOAD_DIR` 环境变量定位文件，staged 迁移
时上传文件尚在 restore 临时目录，回填可能落空（storage_key 保持 NULL）——与现行
"restore 后由应用按文件名回退定位"的行为一致，可接受，不在本期处理。

### 3.3 测试夹具重建

- 新增 module 级夹具：用 Alembic 对临时文件执行一次 `upgrade head` 生成模板库，
  缓存文件路径；`_build_backup_tar` 每次复制模板库再插入测试数据，manifest 写入
  真实 head revision；
- 特例构造保留参数化入口：`schema="legacy"`（保留现手写旧 schema 逻辑，无
  `alembic_version` 表）、`schema="0001"`（对空库执行 `alembic upgrade 0001` 生成）、
  `revision_override`（manifest 版本篡改用例）。

### 3.4 测试用例

1. 现有全部用例改走 head 版本备份 → 行为不变且不再污染测试库；
2. 旧版备份（0001）恢复成功：响应 200，恢复后库 revision == head 且
   `documents.storage_key` 列存在；
3. legacy 备份（无 `alembic_version`）→ 400，且当前库文件未被替换（恢复前后
   revision 不变）；
4. manifest `db_schema_revision` 与 DB 实际版本不一致 → 400；
5. 备份 revision 超前（伪造 "9999_future"）→ 400；
6. 无 manifest 但 DB revision == head → 恢复成功（保留无 manifest 兼容路径）；
7. 回归：`tests/api/` 全量通过，restore 文件不再引发连锁 errors。

## 4. 涉及文件

| 文件 | 改动 |
|------|------|
| `backend/api/backup.py` | 版本判定重写（staged DB 权威 + manifest 交叉校验 + 四分支）、staged 迁移执行 |
| `backend/tests/api/test_restore_integration.py` | 模板库夹具、用例改造与新增 |

## 5. 错误处理与不变量

- 所有拒绝路径复用现有 `HTTPException(400)` 与维护锁释放逻辑；
- staged 迁移/校验失败时，当前数据库、uploads、Qdrant 集合均不被触碰；
- 恢复成功后库 revision 必为 head——应用启动 gate（`check_revision_gate`）不会再
  因恢复引入的旧库拒绝启动。
