# RAG Agent 第三轮优化与实测结果

> 日期：2026-07-20（Asia/Shanghai）  
> 前序：`PROJECT_OPTIMIZATION_PHASE2_RESULT_2026-07-20.md`  
> 明确排除：固定管理员密码策略继续保持不变；93×2 在线评测未触发

## 结论

第三轮无额外模型评测费用的优化已完成。默认后端 Docker 镜像由 CLI 显示的 1.09 GB 降至 688 MB，减少约 402 MB；完整 Docker 验收 12/12 阶段通过。仓库级 Ruff 从 29 个 import 排序问题收口到零错误。最终覆盖率运行通过 756 项测试，生产源码分支覆盖率为 66.52%。

正式 Docker Run ID：`ragagent-e2e-20260720-032455-0ec0ffa7`。

## 1. 镜像层审计与优化

优化前 `docker history` 实测主要层：

```text
apt libgl1 + libglib2.0-0: 216 MB
pip runtime dependencies: 463 MB
application COPY: 8.13 MB
Docker CLI image size: 1.09 GB
```

审计发现：

- `libgl1`、`libglib2.0-0` 服务于可选 OCR/OpenCV 路径；默认镜像没有安装 PaddleOCR/OpenCV Python 依赖，因此该 216 MB 系统层在默认运行路径中无效；
- pandas 仅被 `rag/loaders.py` 用于 CSV/XLSX 转 Markdown；
- 后端测试与大量评测文件不需要进入运行镜像。

完成修改：

- 默认 Dockerfile 移除 `libgl1`、`libglib2.0-0`；
- `requirements.txt` 移除 pandas；
- `.dockerignore` 排除 `tests`；
- E2E 一致性脚本改为验收时通过 `docker cp` 注入 `/tmp`，显式使用 `/app` 作为应用根目录；
- Alembic migrations、应用源码和 tiktoken 离线缓存仍保留在镜像中。

优化后实测：

| 指标 | 优化前 | 优化后 | 变化 |
|---|---:|---:|---:|
| Docker CLI 镜像大小 | 1.09 GB | 688 MB | 约 -402 MB / -36.9% |
| `image inspect` 内容大小 | 267,627,227 B | 171,611,392 B | -35.88% |
| pip layer | 463 MB | 378 MB | -85 MB |
| application COPY | 8.13 MB | 3.13 MB | -61.5% |

运行时容器确认：PyMuPDF、openpyxl、Qdrant client、tiktoken 均可导入，pandas 不存在，Markdown 表格转换可用。

## 2. CSV/XLSX 加载器去 pandas

`rag/loaders.py` 改为：

- CSV：Python 标准库 `csv`，使用 `utf-8-sig` 兼容 BOM；
- XLSX：`openpyxl.load_workbook(read_only=True, data_only=True)`；
- 两者共享确定性 Markdown table renderer；
- 单元格中的 `|` 转义为 `\|`，换行转换为 `<br>`；
- 空文件/空工作表返回空字符串；
- 最多读取 10,000 条数据行，避免大表格一次性占满内存；
- 支持不齐整行并补空单元格。

新增 BOM、Markdown 转义、空文件、行数上限、空 XLSX 和 XLSX 多行单元格测试。

## 3. Docker 验收发现并修复的问题

第一次优化镜像验收通过了 build、health、secrets、auth 和 upload，但一致性探针失败：探针从旧路径 `/app/tests/e2e` 推导应用根目录，复制到 `/tmp` 后 `parents[2]` 越界。

处理方式：

- 保留失败产物 `ragagent-e2e-20260720-030823-78ba1e01` 作为诊断证据；
- 按精确 Run ID 清理失败验收的容器、网络和数据卷；
- 探针增加 `RAG_AGENT_APP_ROOT`，验收脚本以 `/app` 显式注入；
- 增加“tests 被排除时 E2E 必须注入探针”的合同测试；
- Windows PowerShell 5.1 语法解析通过后重新执行完整验收。

最终正式结果：

```text
Run ID: ragagent-e2e-20260720-032455-0ec0ffa7
overall: passed
stages: 12/12 passed
backend image: 688 MB
documents: 2/2 ready
live index consistency: 2 documents / 2 chunks
SSE QA: 2/2 passed
faithfulness / citation precision / citation recall: 全部 1.0
restart persistence: passed
backup restore: 2/2 restored and ready
Qdrant degradation and recovery: passed
Docker smoke: 5/5 passed
wall time: 81.1 s
```

正式产物目录：`artifacts/docker-e2e/ragagent-e2e-20260720-032455-0ec0ffa7/`。验收通过后隔离容器、网络和数据卷已自动清理。

## 4. 仓库级 Ruff 收口

- 修复 29 个 `I001` import 排序问题；
- 使用 Ruff 自动修复，仅调整 import block；
- 修复后执行全仓 `ruff check backend`，结果为 `All checks passed`；
- 随后执行 Mypy 与全量 pytest，验证导入顺序没有造成启动或模块副作用。

## 5. 零覆盖模块补测与缺陷修复

根据旧 `.coverage` 的真实缺口，选择无需外部模型、可以稳定离线验证的四个 0% 模块补测：

| 模块 | 优化前 | 优化后 |
|---|---:|---:|
| `rag/query_rewriter.py` | 0% | 94% |
| `models/fingerprint.py` | 0% | 100% |
| `logging_config.py` | 0% | 98% |
| `worker/ingestion.py` | 0% | 100% |
| `rag/loaders.py` | 26% | 51% |

测试同时发现并修复三个语义问题：

- `rewrite()` 的 `create_llm()` 原本位于异常保护之外，LLM 工厂创建失败时不会按文档回退；现在工厂与流式调用均失败安全并返回空变体；
- `diff_fingerprint()` 原本忽略 expected hash，即使指纹完全一致也返回问题列表；现在匹配时返回空列表，不匹配时同时输出 expected/actual 哈希和现有表信息；
- `setup_logging()` 多次执行会累积重复 console/file handlers；现在只替换本模块管理的 handlers，不干扰第三方日志配置。

新增 9 项定向测试，覆盖查询变体过滤与失败回退、SQLite schema/index 指纹变化、JSON exception 日志、日志幂等性及 ingestion 参数透传。

最终生产源码分支覆盖率由第二轮的 63.14% 提升至 66.52%，门禁仍保持 60%，没有降低阈值或扩大排除范围。

## 6. 最终验证数据

```text
Loader/requirements/Docker contract tests: 32 passed
Backend branch coverage: 756 passed, 18 skipped, 9 deselected, 227.25 s
Production source branch coverage: 66.52%, gate 60% passed
Ruff full backend: All checks passed
Mypy: Success, no issues in 202 source files
PowerShell 5.1 parse: passed
Optimized image runtime imports: passed
Docker E2E: 12/12 stages passed
Docker smoke: 5/5 passed
git diff --check: passed
```

前端代码本轮未修改，沿用第二轮已通过的 lint、build 和 60/60 测试结果。

## 7. 仍未执行的项目

1. 当前代码版本的 93×2 在线生成评测与 provenance 刷新；该步骤会产生 186 次模型生成调用，需要单独确认费用；
2. `api/memories.py`（20%）、`memory/profile.py`（22%）、`api/settings.py`（24%）等大模块的进一步分支测试；
3. 将可选 OCR 制作为独立镜像 target/profile，安装 PaddleOCR/OpenCV 及对应系统库；
4. 继续拆分 `api/settings.py`、`api/backup.py` 等大模块。

固定管理员密码优化按用户要求继续暂缓。

## Claude Code 执行说明

本轮按协作约定创建了严格限制文件范围的 Claude Code 合同并通过 dry-run，但实际调用再次被租户数据外传策略拒绝。没有绕过策略，随后由 Codex 本地实现和独立验证；没有通过 Claude Code 发送仓库内容。
