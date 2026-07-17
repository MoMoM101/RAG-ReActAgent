# RAG Agent 项目状态与待办清单

> 日期：2026-07-17
> 基线：原 `NEXT_PHASE_OPTIMIZATION_EXECUTION_PLAN_2026-07-17.md`
> 进度：Phase 0–5 完成，Phase 6–9 待办

## 已完成阶段

### Phase 0：Docker 封板
- [x] Docker 全链路验收（构建、启动、上传、问答、备份恢复、降级）
- [x] 10 个 Docker 部署阻断项修复
- [x] 前端 Nginx 生产镜像
- [x] 最终干净备份恢复闭环
- [x] 5/5 Docker smoke 通过

### Phase 1：Docker E2E 验收自动化
- [x] `scripts/docker_e2e_acceptance.ps1` — 12-stage 全链路编排脚本
- [x] `docker-compose.e2e.yml` 参数化 + wget 健康检查
- [x] `test_docker_smoke.py` 严格模式（`DOCKER_E2E_REQUIRED=1`）
- [x] `test_live_index_consistency.py` — 只读 BM25/Qdrant chunk-ID 一致性
- [x] CI release gate job（pwsh, 30min, secrets 注入）
- [x] 10 个提交，1100 行脚本

### Phase 2：容量与性能基线工具链
- [x] `scripts/benchmark/generate_fixtures.py` — 5 场景测试文档生成器
- [x] `scripts/benchmark/upload_bench.py` — 批量上传基准
- [x] `scripts/benchmark/qa_bench.py` — 并发问答基准 + SSE 解析
- [x] `scripts/benchmark/collect_metrics.py` — Prometheus + docker stats 采集
- [x] `scripts/benchmark/generate_report.py` — Markdown/JSON/CSV 报告
- [x] `scripts/benchmark/run_all.py` — 编排入口 + skip/resume

### Phase 3a：Ingestion 可靠性
- [x] 4 个重试配置项（max_retries=3, base=5s, max=300s, jitter=0.3）
- [x] 错误分类（rate_limit / transient / permanent）
- [x] 指数退避 + jitter
- [x] 幂等守卫（committed generation 跳过）
- [x] retry_wait 任务状态 + 启动恢复
- [x] 13 个单元测试

### Phase 3b：前端并行上传
- [x] 移除全局 uploading 锁（仅删除 9 行）
- [x] 多批并行上传 + 逐文件独立状态追踪

### Phase 4：RAG 尾延迟优化
- [x] 5 个 Per-phase 超时预算（intent/retrieval/generation/verification/repair）
- [x] 答案缓存默认开启（TTL=300s）
- [x] `run_latency_ab.py` — 4 个 config profile × 93 样本 A/B 对比
- [x] `--env-override` flag 支持不重启切换配置
- [x] 7 个单元测试

### Phase 5a：RBAC + JWT 鉴权
- [x] users 表 + bcrypt 密码哈希
- [x] JWT create/verify/refresh + `auth/jwt.py`
- [x] `security.py` 重写（jwt_auth 中间件 + require_role + legacy 兼容）
- [x] `/api/auth/login` / `refresh` / `me`
- [x] `/api/users/` CRUD（system_admin only）
- [x] 4 角色权限：viewer / editor / knowledge_admin / system_admin
- [x] 路由差异化（viewer+ 可问答，system_admin 管理用户）
- [x] Bootstrap admin 用户（admin/admin123，首次登录需改密码）
- [x] 10 个测试

### Phase 5b：审计日志与脱敏
- [x] audit_logs 表 + `record_audit()` helper
- [x] 11 个高风险端点 wiring（login/upload/delete/clear/backup/restore/user CRUD）
- [x] 日志脱敏（Authorization header、token/secret/key 参数 mask）
- [x] `GET /api/audit/` 查询 API（system_admin only）

---

## 待办阶段

### Phase 6：数据层与多实例部署
**状态：方案待决策**

- [ ] **选项 A**：抽象准备（Alembic + DB 抽象层 + 配置化切换），不改 PostgreSQL
- [ ] **选项 B**：完整 PostgreSQL 迁移（安装 PG、asyncpg、docker-compose +PG 服务）
- [ ] **选项 C**：跳过，先做 Phase 7

详见 `docs/NEXT_PHASE_OPTIMIZATION_EXECUTION_PLAN_2026-07-17.md` 第 9 节。

### Phase 7：可观测性、SLO 与告警
**状态：未开始**

- [ ] 完善 metrics 覆盖（SSE 打开/断开/done 率、cache hit/miss、repair trigger/accept）
- [ ] 文档队列深度、最老任务年龄、dead-letter 监控
- [ ] 全链路 request_id → conversation_id → document_id → generation_id → task_id
- [ ] Trace span 层级（chat.request → intent → retrieval → llm → verify → repair）
- [ ] SLO 定义（月可用性 99.5%、P95 < 6s 告警、dead-letter 告警）
- [ ] Grafana dashboard（已有 `docs/grafana_v4_rag_dashboard.json` 基础）

### Phase 8：灾备、发布门禁与供应链
**状态：未开始**

- [ ] 定时备份 SQLite + 上传文件 + active collection 指针
- [ ] Qdrant 快照策略
- [ ] 备份加密、SHA-256 校验、保留周期
- [ ] 每月自动恢复到隔离环境
- [ ] PR/CI 门禁（lint + type check + test + Docker build + compose smoke）
- [ ] 发布门禁（93 样本在线评测 + 容量基线 + 质量门禁）
- [ ] Secret scan + dependency scan + image scan
- [ ] 发布证据包（SBOM, vulnerability report, migration plan, rollback plan）

### Phase 9：灰度上线与持续评测
**状态：未开始**

- [ ] 灰度顺序：开发 → 隔离验收 → 内部 5-10 人 → 单部门 → 10% → 50% → 全量
- [ ] 每级观察窗口：最少 24h，检查错误率/TTFT/引用质量/拒答/上传失败
- [ ] 每周从匿名真实请求抽取候选样本
- [ ] 错误类型分桶（检索失败/来源错误/引用缺失/不忠实/误拒答/超时/工具异常）
- [ ] 每次优化说明改善/回归了哪个桶

---

## 关键文件索引

### 脚本与工具
| 文件 | 用途 |
|---|---|
| `scripts/docker_e2e_acceptance.ps1` | 12-stage Docker 全链路验收 |
| `scripts/benchmark/generate_fixtures.py` | 5 场景测试文档生成 |
| `scripts/benchmark/upload_bench.py` | 批量上传基准 |
| `scripts/benchmark/qa_bench.py` | 并发问答基准 |
| `scripts/benchmark/collect_metrics.py` | 系统指标采集 |
| `scripts/benchmark/generate_report.py` | 容量报告生成 |
| `scripts/benchmark/run_all.py` | 基准编排入口 |

### 后端核心改动
| 文件 | Phase | 改动 |
|---|---|---|
| `backend/config.py` | P2/3a/4/5a | 新增 15 个配置项 |
| `backend/rag/pipeline.py` | P3a | 错误分类、退避、幂等守卫 |
| `backend/worker/tasks.py` | P3a | retry_wait 状态机 |
| `backend/agent/loop.py` | P4 | 4 处超时 wiring |
| `backend/agent/tools.py` | P4 | 检索超时 |
| `backend/security.py` | P5a | JWT + RBAC 重写 |
| `backend/auth/jwt.py` | P5a | JWT 签发/验证/bcrypt |
| `backend/api/auth.py` | P5a | login/refresh/me |
| `backend/api/users.py` | P5a | 用户 CRUD |
| `backend/api/audit.py` | P5b | 审计日志查询 |
| `backend/audit.py` | P5b | record_audit helper |
| `backend/middleware/logging.py` | P5b | 日志脱敏 |
| `backend/main.py` | P4/5a/5b | 路由 wiring + bootstrap |

### 前端
| 文件 | Phase | 改动 |
|---|---|---|
| `frontend/src/stores/documentStore.ts` | P3b | 移除全局上传锁 |

### 测试
| 文件 | Phase | 测试数 |
|---|---|---|
| `backend/tests/e2e/test_docker_smoke.py` | P1 | 5 |
| `backend/tests/e2e/test_live_index_consistency.py` | P1 | 4 |
| `backend/tests/test_timeout_budget.py` | P4 | 7 |
| `backend/tests/rag/test_ingestion_reliability.py` | P3a | 13 |
| `backend/tests/api/test_auth.py` | P5a | +6 JWT tests |
| `backend/tests/api/test_users.py` | P5a | 4 |
| `backend/tests/run_latency_ab.py` | P4 | A/B runner |

### CI / 部署
| 文件 | 用途 |
|---|---|
| `.github/workflows/release-gate.yml` | Docker E2E acceptance job |
| `docker-compose.e2e.yml` | E2E 参数化覆盖 |

### 设计文档
| 文件 | Phase |
|---|---|
| `docs/superpowers/specs/2026-07-17-docker-e2e-automation-design.md` | P1 |
| `docs/superpowers/specs/2026-07-17-capacity-baseline-design.md` | P2 |
| `docs/superpowers/specs/2026-07-17-ingestion-reliability-design.md` | P3a |
| `docs/superpowers/specs/2026-07-17-rag-latency-optimization-design.md` | P4 |
| `docs/superpowers/specs/2026-07-17-rbac-jwt-design.md` | P5a |
| `docs/superpowers/specs/2026-07-17-audit-logging-design.md` | P5b |

---

## 40 个提交摘要

```
# Phase 1 (10 commits)
2ff3763..d670647: Docker E2E automation + compose + CI + consistency tests

# Phase 2 (8 commits)
5376e0c..5c96c9c: 6 benchmark tools + fixtures + .gitignore

# Phase 3a (5 commits)
fc3b26e..a13fca5: error classification + backoff + idempotency + retry_wait + tests

# Phase 3b (1 commit)
48085c7: remove global upload lock (9 lines deleted)

# Phase 4 (5 commits)
cd08c3f..8e4ae1f: timeout budgets + cache + A/B runner + tests

# Phase 5a (7 commits)
c3b18a3..4615ef1: JWT + RBAC + user management + tests

# Phase 5b (4 commits)
035df32..08b6769: audit logging + sanitization + query API
```

## 下次启动

```bash
cd D:/Python/subject1/RAG_Agent
# 查看完整分支状态
git log --oneline -40
# 阅读下一步计划
cat docs/PROJECT_STATUS_2026-07-17.md
# 从 Phase 6 决策开始
```
