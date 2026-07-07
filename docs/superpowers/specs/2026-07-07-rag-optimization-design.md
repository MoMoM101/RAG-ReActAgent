# RAG 检索优化设计

> 日期：2026-07-07
> 范围：FTS5 trigram 迁移、Qdrant 单例健壮化、检索评测方法改进

---

## 一、FTS5 迁移至 trigram tokenizer

### 问题

当前 `textdb/sqlite_fts.py` 使用 `unicode61` tokenizer + CJK 空格分割。存储时在 CJK 字符间插入空格，导致单字 token 区分度极低，中文关键词搜索几乎无效。RAG 评测中 FTS5 贡献率长期为 0。

### 方案

将 FTS5 tokenizer 从 `unicode61` 改为 `trigram`。trigram 按 3 字符滑动窗口切分，语言无关，无需手动分词。

**建表变更**（`models/database.py` `init_db()`）：

```sql
-- 旧
CREATE VIRTUAL TABLE IF NOT EXISTS chunks_fts USING fts5(chunk_id, document_id, content, tokenize=unicode61)

-- 新
CREATE VIRTUAL TABLE IF NOT EXISTS chunks_fts USING fts5(chunk_id, document_id, content, tokenize=trigram)
```

**存储变更**（`sqlite_fts.py` `insert()`）：
- 移除 `_segment_cjk()` 调用，trigram 原生处理 CJK
- `_escape_sql()` 仍然需要，防 SQL 注入

**搜索变更**（`sqlite_fts.py` `search()`）：
- 移除 `_segment_cjk()` 调用
- 新增短查询兜底：查询长度 ≤ 2 字符时 trigram 无法生成 token，降级为 SQL `LIKE '%query%'`
- `_escape_fts5()` 保持不变（去掉 `" * ^ ( )` 等 FTS5 特殊字符）

### 向后兼容

tokenizer 变更后已存在的 `chunks_fts` 表不兼容。`init_db()` 需检测现有表并 DROP 重建。知识库清空操作也会触发重建。

### 测试

- `test_insert_and_search` — 适配无 CJK 空格预处理
- `test_cjk_multi_char_and_search` — 重写为 trigram 模式
- 新增 `test_trigram_short_query_fallback` — ≤2 字符查询走 LIKE
- 新增 `test_trigram_english_query` — 英文正常工作
- 新增 `test_trigram_mixed_chinese_english` — 中英混合

### 不变的部分

- `_escape_fts5()` — 特殊字符清洗逻辑不变
- `_safe_id()` / `_escape_sql()` — 不变
- `delete_by_document` / `delete_by_chunks` / `count` — 不变
- FTS5 表结构（列定义）不变，仅 tokenizer 参数变

---

## 二、Qdrant 单例健壮化

### 问题

`vectordb/qdrant.py` 中 `_get_client()` 使用简单的 `is None` 判断：
1. Qdrant 服务重启后 gRPC channel 失效，不会自动重建
2. 测试中切换 `qdrant_path` 后 `_client` 仍指向旧路径
3. 残留 3 个 Qdrant 数据目录（`qdrant/`、`qdrant2/`、`test_qdrant/`）

### 方案

状态感知的懒加载，跟踪 client 对应的路径和健康状态：

- 新增 `_client_path` 和 `_client_healthy` 跟踪变量
- `_get_client()` 在路径变化或连接不健康时自动重建
- 搜索调用侧捕获连接异常，标记不健康并抛出 `RetryableError`，Agent 层指数退避重试触发重建

涉及的异常类型判断沿用 `_is_retryable_exception()` 的模式，新增 gRPC 连接相关异常的识别。

### 清理

- 删除 `backend/data/qdrant/`、`backend/data/qdrant2/`、`backend/data/test_qdrant/`
- `.gitignore` 新增 `backend/data/qdrant*/`
- 只保留 `settings.qdrant_path` 指定的单一数据目录

### 测试

- `conftest.py` 新增 `reset_qdrant_client()` fixture
- `test_qdrant.py` 新增 `test_client_rebuild_on_path_change`
- 新增 `test_client_rebuild_on_connection_error`

### 不变的部分

- `QdrantVectorDB` 类的公开接口不变
- `create_vectordb()` factory 签名不变
- 搜索/写入/删除接口不变

---

## 三、检索评测方法改进

### 问题

当前评测使用 Jaccard ≥ 0.25 单阈值判定相关性，区分度不足。且只对比了"有/无 reranker"二元维度，无法分离各检索策略的增量贡献。

### 方案

#### 3.1 分层相关性判定

```
2分（精确命中）：检索 chunk_id 在标注的 chunk 范围内
1分（语义相关）：LLM 判定检索文本能够回答查询问题
0分（不相关）：都不满足

strict Precision@k  — 仅计入 2 分
relaxed Precision@k — 计入 2+1 分
```

LLM 判定采用批量调用：一次请求传所有 `(query, retrieved_text)` pair，返回 `[0,1,0,0,1]` 数组。

#### 3.2 消融对比

每个查询跑 4 种策略并对比：

| 策略 | 说明 |
|------|------|
| semantic-only | 仅 Qdrant 语义搜索 |
| keyword-only | 仅 FTS5 trigram 关键词搜索 |
| hybrid-no-rerank | 混合 + RRF，无重排序 |
| hybrid-rerank | 混合 + RRF + reranker |

输出消融对比表，直观展示每种策略的增量贡献。

#### 3.3 可复现性

- 每次评测：清理 → 摄入 → 评测 → 输出 JSON + Markdown
- JSON 带时间戳和配置快照（chunk_size、embedding_model、tokenizer 类型）
- 支持 `--compare <previous.json>` 对比两次跑分

### 改动范围

| 文件 | 改动 |
|------|------|
| `backend/tests/evaluate_rag.py` | 重构：消融对比 + LLM 分层判分 + `--compare` 参数 |
| `docs/RAG_RETRIEVAL_EVALUATION.md` | 更新评测方法和结果 |

### 不变的部分

- 测试文档和查询用例库保留
- 评测指标（Precision/Recall/MRR/NDCG/Hit Rate）保留
- 独立运行，不影响生产代码

---

## 实施优先级

| 阶段 | 内容 | 预估 |
|------|------|------|
| Phase 1 | FTS5 trigram 迁移 | 核心改动 |
| Phase 2 | Qdrant 单例健壮化 | 基础设施 |
| Phase 3 | 评测方法改进 | 验证工具 |

Phase 1 和 Phase 2 独立，可并行；Phase 3 依赖 Phase 1 完成后跑分验证。
