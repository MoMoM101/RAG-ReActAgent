# Phase 8a: Scheduled Backup Automation

> 日期：2026-07-18
> 决策：Option A — 定时备份脚本 + 轮转，不做安全扫描/发布证据包
> 基线：Phase 0–7 完成，664/664 测试通过

## 1. 目标

提供独立于 FastAPI 的定时备份脚本，cron / Task Scheduler 可直接调用。备份 SQLite 数据库 + 上传文件 + Qdrant collection 指针，支持按保留天数轮转。

## 2. 设计

### 2.1 脚本入口

```bash
python scripts/scheduled_backup.py \
  --backup-dir ./data/backups \
  --retention-days 7 \
  --db-path ./data/rag_agent.db \
  --upload-dir ./data/uploads \
  --qdrant-path ./data/qdrant2
```

所有参数都有默认值，最小调用只需 `python scripts/scheduled_backup.py`。

### 2.2 备份内容

| 组件 | 来源 | 说明 |
|---|---|---|
| SQLite DB | `--db-path` | `rag_agent.db` 文件，备份前执行 `PRAGMA wal_checkpoint(TRUNCATE)` 确保一致性 |
| Uploads | `--upload-dir` | 整个目录树 |
| Qdrant 指针 | `--qdrant-path` | `active_collections.json`（运行时 collection 名称映射） |

### 2.3 产出物

```
data/backups/
  backup-2026-07-18T120000.tar.gz         # 备份包
  backup-2026-07-18T120000.tar.gz.sha256  # SHA-256 校验和
  backup-2026-07-18T120000.json           # manifest（revision, app_version, files, checksums）
```

### 2.4 轮转

脚本完成后遍历 `--backup-dir`，删除 `backup-*.tar.gz` 中 `mtime` 超过 `--retention-days` 天的文件及对应 `.sha256` 和 `.json`。

### 2.5 代码结构

```
scripts/
  scheduled_backup.py   → 新建 ~80 行
```

内部复用 `backend/api/backup.py` 的 `_build_manifest()` 和 `_sqlite_db_path()` 逻辑，但以独立脚本方式组织，不 import FastAPI。

核心函数：
- `checkpoint_db(db_path)` — WAL checkpoint
- `build_tar(source_db, source_uploads, source_qdrant, dest)` — 打包
- `build_manifest(db_path, upload_dir)` — 复用现有 manifest 逻辑
- `rotate_backups(backup_dir, retention_days)` — 清理旧备份

## 3. 不做的

- 不加密（单机部署，本地文件权限）
- 不上传到远程存储
- 不恢复后自动验证（留给人工或 CI）
- 不改动现有 `api/backup.py`

## 4. 验证

- 脚本空运行（`python scripts/scheduled_backup.py --help`）
- 创建备份，验证 tar.gz 包含 3 项内容
- 验证 manifest.json 含 revision + SHA-256
- 创建过期备份文件，验证轮转删除
- 664 测试无回归
