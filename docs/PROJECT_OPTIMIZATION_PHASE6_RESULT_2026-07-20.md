# 项目优化第六阶段结果（2026-07-20）

## 结论

本阶段按既定顺序完成了工作区分类、Settings/Memories 测试补强、Backup 恢复准备服务拆分、Agent 工具执行状态化，以及后端、前端和 Docker 全量复验。

当前代码正确性、离线工程门禁与 Docker 运行门禁均通过。固定管理员密码策略按用户要求保持不变；Claude 调用保持关闭；93×2 在线评测尚未执行，因此没有产生 186 次批量付费模型生成调用。

## 1. 工作区整理

- 审计时工作区包含 98 个已跟踪修改和 134 个新增路径，混合源码、测试、评测报告与本地工具数据；
- `.claude/`、`.eval-tmp/`、`.playwright-mcp/` 已作为纯本地工具目录加入 `.gitignore`；
- 未删除历史在线评测 JSON、设计文档或用户数据；
- 提交前继续通过文件体积、敏感信息和 diff 格式检查筛选应纳入版本控制的内容。

## 2. Settings 与 Memories 测试

新增 `api/memories.py` 的画像扁平化、类型过滤、读取、更新、删除、404、SQLite/Qdrant 联动清空和空数据清空测试。

新增 `api/settings.py` 的环境读取与密钥掩码、配置持久化、API Key 加密、空库维度不匹配自动重建、维度探测失败、重建状态和并发拒绝测试。

定向结果：

| 模块 | 分支覆盖率 |
|---|---:|
| `api/memories.py` | 98% |
| `api/settings.py` | 30% |
| 两模块定向测试 | 21/21 通过 |

## 3. Backup 恢复服务拆分

新增 `api/backup_restore.py`，以 `PreparedRestoreArchive` 显式表达已经验证的恢复输入，并集中负责：

- 分块限制读取和安全解包；
- manifest JSON、文件哈希和兼容性验证；
- SQLite 完整性检查；
- Alembic revision 交叉验证、版本分类和旧版本暂存迁移；
- ready 文档及内容寻址 `storage_key` 装载。

`api/backup.py` 从 826 行降至 728 行，新模块 172 行。恢复路由继续保持原维护锁、暂存、Qdrant 重建、交叉一致性、原子切换和回滚顺序。相关备份/恢复测试 49/49 通过。

## 4. Agent Loop 状态化

新增 `agent/loop_tools.py`，使用 `ToolTurnState` 和 `ToolTurnOutcome` 显式描述一次工具执行状态转换，集中处理：

- 并行工具执行；
- `tool_call` / `tool_result` 事件构建；
- 跨轮来源编号和引用去重；
- 不可信检索内容包装；
- 消息裁剪、检索/重排耗时记录和重叠来源裁剪。

`agent/loop.py` 从 1291 行降至 1158 行，新模块 209 行。工具注册表以显式依赖传入，保留既有测试注入点和 SSE 事件顺序。Agent/流式相关测试 57/57 通过。

## 5. 测试稳定性修复

完整覆盖率运行发现 `test_get_status_shows_running` 使用固定 `sleep(0.05)` 推测后台任务状态，在高负载下偶发失败。测试现改为：

1. 等待任务协程显式设置 `started` 事件；
2. 释放任务后直接等待返回的 task 完成。

Worker 定向覆盖率测试 14/14 通过，最终全量测试不再出现该时序波动。

## 6. 最终工程门禁

测试日期：2026-07-20（Asia/Shanghai）。

| 门禁 | 实际结果 |
|---|---:|
| Ruff（全 backend） | 通过 |
| Mypy | 220 个源码文件通过 |
| Pytest 收集 | 823 |
| Pytest 结果 | 796 通过、18 跳过、9 排除 |
| 生产代码分支覆盖率 | 70.52% |
| 分支覆盖率门槛 | 60%（通过） |
| 前端 Vitest | 60/60 通过 |
| 前端 Oxlint | 通过 |
| 前端 TypeScript + Vite 构建 | 通过 |

分支覆盖率由第五阶段的 69.07% 提升到 70.52%，未降低门槛或扩大覆盖率排除范围。

## 7. Docker E2E

最终 Run ID：`ragagent-e2e-20260720-120631-95a1474c`，12/12 阶段通过。原始证据位于 `artifacts/docker-e2e/ragagent-e2e-20260720-120631-95a1474c/`。

| 阶段 | 结果 | 耗时 |
|---|---:|---:|
| config_check | 通过 | 6.41s |
| build | 通过 | 103.37s |
| health | 通过 | 14.30s |
| secrets_check | 通过 | 1.23s |
| auth_check | 通过 | 0.10s |
| upload | 通过 | 5.26s |
| consistency | 通过 | 4.34s |
| sse_qa | 通过 | 9.75s |
| restart_persistence | 通过 | 15.98s |
| backup_restore | 通过 | 12.71s |
| degradation | 通过 | 10.78s |
| smoke | 通过，5/5 | 6.93s |

验收上传 2 个文档并生成 2 个分块；两道 SSE 问答的来源、验证和完成事件全部存在，faithfulness、citation precision、citation recall 均为 1.0。重启后 2/2 文档保持 ready；清空后从 11,623 字节备份恢复 2/2 文档及 2 个向量分块；Qdrant 故障时 SQLite 保持可用，恢复后依赖健康。后端镜像为 688MB，容器内不存在 `/app/.env`。成功后隔离容器、网络和数据卷已自动清理。

## 8. 尚未执行

1. 93×2 在线 RAG 评测：涉及 186 次付费模型生成调用，必须在用户再次确认后执行；
2. 固定管理员密码策略：按用户明确要求暂缓；
3. OCR 独立镜像 profile：仅在正式启用 OCR 时建设，不阻塞当前版本。
