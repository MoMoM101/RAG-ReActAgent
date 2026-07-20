# 项目优化第五阶段结果（2026-07-20）

## 结论

本阶段按既定顺序完成了文件存储抽象迁移、Settings/Profile 失败补偿、Agent Loop 第一轮拆分和 Backup 第一轮拆分。当前源码通过后端全量分支覆盖率门禁、Mypy、Ruff，以及前端测试、Lint 和生产构建。

固定管理员密码策略未修改；Claude 调用保持关闭；93×2 在线评测未执行，因此本阶段没有产生186次模型生成调用。

## 1. 文件存储抽象迁移

新上传链路现在统一使用 `FileStorage.create_staging()`、`append()`、`commit()`：

- `Document.storage_key` 在文件提交后与数据库记录一起持久化；
- 文档处理通过后端中立的 `materialize()` 获得本地可读路径；
- 本地存储直接返回受路径校验保护的文件路径，远程/内存后端使用临时文件物化；
- 删除、批量清空、重处理和服务重启恢复优先使用 `storage_key`；
- 旧扁平文件按文件哈希安全定位并导入内容寻址目录，避免同名文件误匹配；
- `settings/clear-all-data` 通过存储后端的 `clear()` 清理对象和暂存数据；
- 保留 `ingest_document_from_path()` 作为旧调用兼容适配层。

新增本地后端与内存后端的统一合同测试，覆盖暂存、提交、读取、删除、清空、路径导入和非本地物化。

测试基础设施同时增加数据库表和存储对象的逐测试清理，消除了恢复测试可能调度前一用例未完成文档的状态串扰。

## 2. Settings 与 Profile 失败补偿

集合重建增加了跨索引补偿切换：

- 新 Qdrant collection 写入完成后才进入激活阶段；
- 如果 BM25 原子切换失败，恢复旧 Qdrant 运行时指针和持久化指针；
- 保留旧 collection，并清理失败的新 collection；
- BM25 成功后才删除旧向量 collection；
- 活跃 collection 指针采用临时文件加 `os.replace()` 原子写入。

用户画像索引增加脏状态保护：数据库画像保存成功但向量索引失败时，标记 `PROFILE_INDEX_DIRTY`；搜索会绕过可能陈旧的 Qdrant 结果，直接使用当前数据库画像计算结果。索引或空画像清理成功后恢复干净状态。

## 3. Agent Loop 拆分

`backend/agent/loop.py` 从约1456行降至1291行，减少约11.3%。新增：

- `agent/loop_setup.py`：规则/LLM 意图分类、记忆拦截、确认、保存和画像召回；
- `agent/loop_support.py`：流式单元验证、确定性引用修复和答案缓存键构造。

原 `agent.loop._verify_stream_unit` 等测试和调用入口继续以兼容别名存在。生成、工具调用、超时和上下文恢复状态机仍保留在主循环，避免一次重构改变流式事件顺序。

## 4. Backup 拆分

`backend/api/backup.py` 从约1162行降至826行，减少约28.9%。新增：

- `api/backup_schema.py`：恢复数据类型、Alembic版本分类/迁移、清单生成、哈希校验和兼容性检查；
- `api/backup_lifecycle.py`：中断恢复目录清理、Qdrant临时集合审计和恢复后集合保留策略。

兼容入口继续从 `api.backup` 导出。拆分测试发现 Alembic 的 `script_location` 依赖执行工作目录，现已改成绝对路径，仓库根目录与 backend 目录运行结果一致。

Docker 实测进一步发现，恢复流程仍按旧版 `uploads/<filename>` 扁平布局查找文件，而新存储后端使用 `storage_key` 内容寻址布局。现已统一通过安全路径解析器优先读取 `storage_key`，并保留旧版扁平文件回退；跨存储一致性检查也兼容没有 `storage_key` 列的旧数据库。新增内容寻址和旧布局回退测试后，备份/恢复相关测试 49/49 通过。

## 5. 实测门禁

测试日期：2026-07-20（Asia/Shanghai）。

| 门禁 | 实际结果 |
|---|---:|
| Ruff（全 backend） | 通过 |
| Mypy | 217个源码文件通过 |
| Pytest 收集 | 810 |
| Pytest结果 | 783通过、18跳过、9排除 |
| 生产代码分支覆盖率 | 69.07% |
| 分支覆盖率门槛 | 60%（通过） |
| 前端 Vitest | 60/60通过 |
| 前端 Lint | 通过 |
| 前端生产构建 | 通过 |
| `git diff --check` | 通过 |

生产代码分支覆盖率由第四阶段的67.21%提升到69.07%，未降低门槛或扩大覆盖率排除范围。

## 6. Docker E2E 状态

当前源码已完成新的 12 阶段容器验收，Run ID：`ragagent-e2e-20260720-111705-74c8c557`，总结果为通过。原始证据位于 `artifacts/docker-e2e/ragagent-e2e-20260720-111705-74c8c557/`。

| 阶段 | 结果 | 耗时 |
|---|---:|---:|
| config_check | 通过 | 3.61s |
| build | 通过 | 3.73s |
| health | 通过 | 12.34s |
| secrets_check | 通过 | 0.74s |
| auth_check | 通过 | 0.08s |
| upload | 通过 | 5.43s |
| consistency | 通过 | 2.08s |
| sse_qa | 通过 | 12.76s |
| restart_persistence | 通过 | 15.53s |
| backup_restore | 通过 | 15.89s |
| degradation | 通过 | 10.38s |
| smoke | 通过，5/5 | 11.05s |

验收上传 2 个文档并生成 2 个分块；重启后 2/2 文档保持 ready。备份后清空全部文档，再从 11,635 字节备份恢复，2/2 文档及 2 个向量分块恢复成功，恢复后 SSE 来源与验证事件再次通过。Qdrant 停止时 SQLite 保持可用，Qdrant 重启后健康状态恢复。后端镜像大小为 688MB，容器内未发现 `/app/.env`。成功后本次测试容器、网络和数据卷已自动清理。

首次运行 `ragagent-e2e-20260720-110538-e8ccdc2b` 在 `backup_restore` 阶段失败，直接暴露了内容寻址迁移后的恢复路径回归；修复与测试补齐后，上述新 Run 全阶段通过。失败 Run 的隔离容器、网络和卷也已按精确 Run ID 清理。

## 7. 后续仍可优化

1. 将 `agent/loop.py` 剩余的生成、工具执行和最终验证状态机改造成显式状态对象；
2. 将 `api/backup.py` 的恢复端点编排继续拆成 restore service，但需保持现有回滚顺序；
3. 补 `api/settings.py` 路由编排分支和 `api/memories.py` API 测试；
4. 需要OCR时再建设独立 OCR 镜像 profile。
