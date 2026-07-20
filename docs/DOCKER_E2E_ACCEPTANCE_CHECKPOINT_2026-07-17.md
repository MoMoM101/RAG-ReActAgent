# Docker 全链路部署验收：执行记录与续跑节点

> 日期：2026-07-17  
> 状态：**主体链路已通过，按额度要求暂停在“最终干净备份恢复复测”之前**  
> 隔离 Compose project：`ragagent-e2e`  
> 后端：http://127.0.0.1:18000  
> 前端：http://127.0.0.1:15173  
> 测试管理令牌：`rag-agent-e2e-admin-token`（仅用于本机隔离验收，不用于生产）

## 1. 本轮目标

用真实 Docker 网络、容器镜像、SQLite 持久卷、Qdrant 持久卷和在线模型完成以下验收：

1. 镜像构建与敏感文件隔离。
2. 后端、前端、Qdrant 启动和健康检查。
3. 管理鉴权与前端反向代理。
4. 知识库批量上传、解析、切分、向量化、BM25 和 Qdrant 写入。
5. 经过前端代理的真实 SSE RAG 问答、来源和验证事件。
6. 容器重启后的数据持久化。
7. 备份、清空、恢复和跨存储一致性。
8. Qdrant 故障降级与恢复。
9. 生产前端镜像，而不是 Vite 开发服务器。

## 2. 当前保留节点

当前**不要执行 `docker compose down -v`**。隔离栈和卷被有意保留，便于下次直接继续。

当前数据状态：

| 项目 | 当前值 |
|---|---:|
| 文档数 | 2 |
| ready 文档数 | 2 |
| 每份文档分块 | 1 |
| embedding 实际维度 | 1024 |
| 前端运行方式 | Nginx 1.27.5 |
| 后端端口 | 18000 |
| 前端端口 | 15173 |

当前两份验收文档：

- `docker_acceptance_product.txt`，document id：`b8832842-43fc-47d7-8475-69e470c871ac`
- `docker_acceptance_policy.md`，document id：`fa2a1f2f-10b5-4628-b488-6f5ff2558542`

固定测试文件位于：

- `backend/tests/e2e/fixtures/docker_acceptance_product.txt`
- `backend/tests/e2e/fixtures/docker_acceptance_policy.md`

## 3. 已执行并通过的验收

### 3.1 构建与镜像安全

- 后端和前端镜像构建成功。
- 新增 `backend/.dockerignore`，排除 `.env`、数据目录、虚拟环境、评测结果等。
- 新增 `frontend/.dockerignore`，排除 `.env`、`node_modules`、`dist` 等。
- 后端构建上下文约 4 MB；修复后增量上下文进一步降至数百 KB。
- 容器内确认 `/app/.env` 不存在：`env-file-absent`。

### 3.2 健康、鉴权与代理

- `/api/health`：200。
- 未携带管理令牌访问 `/api/documents`：401。
- 携带验收令牌访问 `/api/documents`：200。
- Docker smoke：`5 passed`。
- Nginx 前端首页：200。
- Nginx `/api` 代理可列出 2 份文档。
- 响应头确认 `Server: nginx/1.27.5`、`X-Content-Type-Options: nosniff`。

### 3.3 批量上传与索引

- 通过前端 Nginx 代理调用 `/api/documents/upload-batch`。
- 一次上传 2 个文件，`succeeded=2`、`failed=0`。
- 两份文档最终均为 `ready`，各 1 个 chunk。
- 已验证 Qdrant 和 BM25 均能保存两份文档的条目。

### 3.4 SSE RAG 问答

恢复后的真实知识库问题：企业年度订阅多少天内可全额退款。

结果：

- HTTP 200。
- `tool_call`、`tool_result`、`answer_chunk`、`sources`、`verification`、`done` 事件完整。
- 回答：七个自然日，并引用 `[S1]`。
- Faithfulness：1.0。
- Citation precision：1.0。
- Citation recall：1.0。
- 客户端端到端耗时约 4.93 秒；后续 Nginx 最终复测约 4.25 秒。
- 服务端 `rag_total`：约 3.65 秒。

### 3.5 重启持久化

- 同时重启 backend 与 Qdrant 后，2 份文档和 2 个向量点仍存在。
- 人工注入 stale generation 后重启：
  - active BM25 两份文档均保留；
  - stale generation 被标记为 `failed`；
  - `error_stage=startup_cleanup`；
  - 未误删 active generation 数据。

### 3.6 降级与恢复

- 停止 Qdrant 后，`/api/health/dependencies` 在约 1.55 秒内返回：
  - overall：`error`
  - qdrant：`error`
  - sqlite：`ok`
- 重新启动 Qdrant 后健康恢复。

### 3.7 回归测试

- 本轮新增的 3 个精确回归：`3 passed`。
- 工具、备份恢复、generation 可见性三组完整回归：`60 passed in 12.80s`。
- 早期一次运行曾为 `59 passed, 1 failed`，该失败发现并修复了 staging SQLite 校验错误；修复后 60/60 通过。

## 4. 本轮发现并修复的问题

### 4.1 `.env` 和本地数据可能被打进镜像

原因：Dockerfile 使用 `COPY . .`，项目原先没有 `.dockerignore`。

修复：新增前后端 `.dockerignore`，明确排除凭据、数据、缓存、虚拟环境和大体积评测输出。

### 4.2 加密 API Key 导致容器启动 `InvalidTag`

原因：Compose 注入了 `ENC:` 凭据，但没有注入加密时使用的 `SECRET_KEY`。

修复：`docker-compose.yml` 将 `SECRET_KEY` 传入后端容器。

### 4.3 Qdrant 端口配置错误

原因：代码使用 `QdrantClient(host=..., port=...)` 的 HTTP 客户端，但 Compose 传入 6334（gRPC）。

修复：后端 `QDRANT_PORT` 改为 6333。

### 4.4 可选 OCR 让基础镜像健康状态变红

原因：基础镜像只安装 `requirements.txt`，未安装 `requirements-ocr.txt`，但 OCR 默认开启。

修复：Docker 基础部署默认 `OCR_ENABLED=false`；如需 OCR，使用包含 OCR 依赖的定制镜像并设置 `DOCKER_OCR_ENABLED=true`。

### 4.5 SSE 中途崩溃且客户端一直等待

原因：生产 requirements 缺少 `jsonschema`；ImportError 后异常分支又引用未绑定的 `jsonschema`，触发 `UnboundLocalError`。

修复：

- `backend/requirements.txt` 增加 `jsonschema>=4.20,<5`。
- `_validate_tool_params` 对 ImportError 安全降级。
- 新增缺依赖回归测试。

### 4.6 恢复时 active collection 指针目录不存在

原因：远程 Qdrant 模式下 `/app/data/qdrant` 不会自然创建，写 `active_collections.tmp` 失败。

修复：写指针前执行 `ptr_file.parent.mkdir(parents=True, exist_ok=True)`，并新增回归测试。

### 4.7 恢复依赖 tiktoken 公共 CDN

原因：容器首次切分文档时下载 `cl100k_base`，网络抖动会让恢复失败。

修复：Docker 构建阶段预下载编码，设置 `TIKTOKEN_CACHE_DIR=/app/.cache/tiktoken` 并固化进镜像。

### 4.8 stale generation 清理误删 active 索引

原因：旧 generation 与新 committed generation 可同时存在；启动清理旧记录时按 `document_id` 删除，会连同 active Qdrant/BM25 一起删掉。

修复：清理前读取 `documents.active_generation_id`。若 stale generation 已被另一个 active generation 取代，只标记 stale 失败，不删除文档索引。

### 4.9 恢复一致性检查读错数据库

原因：恢复切换前，BM25 校验读取在线 async engine，而不是 staging SQLite，结果会被在线数据污染。

修复：`_bm25_doc_chunk_counts(..., db_path=staging_db)` 直接读取候选数据库；旧备份没有 BM25 表时兼容跳过，有表时逐文档严格校验。

### 4.10 前端容器使用 Vite 开发服务器

修复：

- `frontend/Dockerfile` 改为 Node 多阶段构建 + Nginx 运行。
- 新增 `frontend/nginx.conf`。
- SPA 路由回退到 `index.html`。
- `/api/` 反向代理到 backend。
- `proxy_buffering off` 支持 SSE。
- `proxy_request_buffering off` 支持流式大文件上传。
- `client_max_body_size 0`，由后端动态上传限制做最终裁决，避免 Nginx 提前 413。

## 5. 本轮新增或修改的关键文件

- `backend/.dockerignore`
- `frontend/.dockerignore`
- `docker-compose.e2e.yml`
- `docker-compose.yml`
- `backend/Dockerfile`
- `frontend/Dockerfile`
- `frontend/nginx.conf`
- `backend/requirements.txt`
- `backend/agent/tools.py`
- `backend/api/backup.py`
- `backend/rag/pipeline.py`
- `backend/tests/agent/test_tools.py`
- `backend/tests/api/test_restore_integration.py`
- `backend/tests/rag/test_generation_visibility.py`
- `backend/tests/e2e/fixtures/*`

注意：工作区在本轮之前已有大量未提交修改。本轮没有 reset、checkout 或覆盖其他用户改动。

## 6. 下次续跑：只剩最终干净备份恢复闭环

先进入项目：

```powershell
Set-Location D:\Python\subject1\RAG_Agent
$compose = @(
  '-p', 'ragagent-e2e',
  '--env-file', 'backend/.env',
  '-f', 'docker-compose.yml',
  '-f', 'docker-compose.e2e.yml'
)
$headers = @{ 'X-Admin-Token' = 'rag-agent-e2e-admin-token' }
```

### 6.1 确认当前节点仍健康

```powershell
docker compose @compose ps
Invoke-RestMethod -Headers $headers `
  http://127.0.0.1:18000/api/health/dependencies | ConvertTo-Json -Depth 6
Invoke-RestMethod -Headers $headers `
  http://127.0.0.1:15173/api/documents | ConvertTo-Json -Depth 6
```

预期：backend/frontend healthy；依赖 `status=ok`；文档 2 个且均为 ready。

### 6.2 生成新的、一致的备份

```powershell
New-Item -ItemType Directory -Force .docker-e2e | Out-Null
curl.exe -sS -f `
  -H 'X-Admin-Token: rag-agent-e2e-admin-token' `
  -o .docker-e2e\backup-final.tar.gz `
  http://127.0.0.1:18000/api/backup

Get-Item .docker-e2e\backup-final.tar.gz | Select-Object FullName, Length
Get-FileHash .docker-e2e\backup-final.tar.gz -Algorithm SHA256
```

### 6.3 清空并恢复

以下操作只针对 `ragagent-e2e` 隔离数据：

```powershell
Invoke-RestMethod -Method Delete -Headers $headers `
  http://127.0.0.1:18000/api/documents/clear-all

curl.exe -sS `
  -H 'X-Admin-Token: rag-agent-e2e-admin-token' `
  -F 'file=@.docker-e2e/backup-final.tar.gz;type=application/gzip' `
  -w "`nHTTP_STATUS:%{http_code}`n" `
  http://127.0.0.1:18000/api/backup/restore
```

验收条件：HTTP 200、`documents_total=2`、`documents_restored=2`。

### 6.4 恢复后核对

```powershell
$docs = Invoke-RestMethod -Headers $headers `
  http://127.0.0.1:15173/api/documents
$docs | ConvertTo-Json -Depth 6
```

要求：2 份文档、均 ready、chunk_count 总计 2。

再通过前端 `/api/chat` 发起以下问题：

> 根据知识库，星河知识平台的标准工单响应时限和紧急工单首次响应时限分别是多少？请引用来源。

SSE 必须包含：

- `event: answer_chunk`
- `event: sources`
- `event: verification`
- `event: done`
- verification 中 Faithfulness、citation precision、citation recall 均为 1.0

### 6.5 最终 smoke

项目 `.venv` 仍指向失效的旧 uv Python；本轮用可用解释器执行：

```powershell
$env:BACKEND_URL = 'http://127.0.0.1:18000'
$env:ADMIN_API_TOKEN = 'rag-agent-e2e-admin-token'
D:\Python\Python\python.exe -m pytest backend/tests/e2e/test_docker_smoke.py -q
```

预期：`5 passed`。

## 7. 最终清理策略

在确认不再需要验收数据前，保持当前栈。

仅停止容器并保留卷：

```powershell
docker compose @compose down
```

确认所有验收证据已记录后，才删除**隔离项目**及其卷：

```powershell
docker compose @compose down -v
```

不要对默认项目名或用户真实部署执行 `down -v`。

## 8. 当前结论

当前代码已经跨过真实 Docker 部署的主要阻断项：镜像安全、加密配置、Qdrant 端口、可选组件健康、SSE 生产依赖、恢复指针、tiktoken 离线缓存、generation 清理竞态、staging 一致性校验和生产前端均已修复并得到实测证据。

质量链路、在线问答、引用验证、重启持久化和故障降级均已通过。唯一尚未在**最后这版 staging BM25 严格校验代码**上重新执行的是“新备份 -> 清空 -> 恢复 -> 再问答”闭环；当前已保留两份干净 ready 文档，下一次从第 6 节开始即可，预计不需要重新构建镜像。
