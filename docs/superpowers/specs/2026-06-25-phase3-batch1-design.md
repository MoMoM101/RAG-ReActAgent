# 三期第一批 — 主题切换 + 检索去重 + Reranker 设计文档

**日期**: 2026-06-25 | **状态**: 已确认

---

## 1. 主题切换

### 1.1 两套主题

CSS 变量驱动，`[data-theme="dark"]` / `[data-theme="light"]`：

| | 深色 | 浅色 |
|------|------|------|
| 背景 | `#0f172a` 深灰蓝 | 米白暖色系 |
| 卡片/面板 | `rgba(255,255,255,0.03)` | 白色 + 浅阴影 |
| 强调色 | 蓝紫 | 蓝紫（保持一致） |
| 主文字 | `#e5e7eb` | `#1e293b` |
| 次要文字 | `#94a3b8` | `#64748b` |
| 边框 | `rgba(255,255,255,0.06)` | `rgba(0,0,0,0.06)` |

### 1.2 交互

- Settings 页面增加主题 toggle（深色/浅色/跟随系统）
- 偏好存 localStorage，`App.tsx` 初始化时读取并挂 `data-theme`
- 首次访问无偏好时跟随系统 `prefers-color-scheme`

### 1.3 文件

| 文件 | 改动 |
|------|------|
| `frontend/src/index.css` | 重构为 CSS 变量 + 两套主题值 |
| `frontend/src/App.tsx` | 初始化 data-theme |
| `frontend/src/components/settings/SettingsPage.tsx` | 加主题 toggle |

---

## 2. 检索去重

### 2.1 场景

用户上传多个版本的文档（如"网络协议.txt"和"网络协议v2.txt"），检索时两个文档的相似片段都被召回。系统检测跨文档高度相似的 chunk，只保留最新文档（`created_at` 最晚）的版本。

### 2.2 流程

```
RRF 融合结果 (top_k * 3 候选)
  → 计算候选间两两文本余弦相似度
  → 相似度 > DEDUP_THRESHOLD 且来自不同 document_id
  → 比较 Document.created_at，保留最新的
  → 去重后的候选集
```

### 2.3 配置

```
DEDUP_SIMILARITY_THRESHOLD=0.90
```

### 2.4 文件

| 文件 | 改动 |
|------|------|
| `backend/rag/retriever.py` | RRF 融合后加去重步骤 |
| `backend/config.py` | 新增 `dedup_similarity_threshold` |

---

## 3. Reranker

### 3.1 架构

新建 `backend/reranker/`，风格与 `embedding/`、`llm/` 一致：

```
backend/reranker/
├── base.py              # BaseReranker 抽象类
├── cross_encoder.py     # sentence-transformers CrossEncoder 实现
└── factory.py           # create_reranker() 工厂
```

### 3.2 检索管线最终流程

```
Query
  → embedding.embed_query(query)
  → Qdrant 语义检索 (top_k * 3)
  → FTS5 BM25 关键词检索 (top_k * 3)
  → RRF 融合 → 候选集 (top_k * 3)
  → 检索去重（按文档时间戳）
  → Reranker Cross-Encoder 精排
  → 返回最终 top_k
```

### 3.3 Cross-Encoder 模型

- 默认 `BAAI/bge-reranker-v2-m3`
- query + chunk_text 拼接后前向传播，输出相关性分数
- 候选集控制在 10-20 条以内保证延迟可控

### 3.4 配置

```
RERANK_ENABLED=true
RERANK_MODEL=BAAI/bge-reranker-v2-m3
RERANK_TOP_N=10
```

### 3.5 retriever.py 改动

`hybrid_search()` 接受可选参数 `use_rerank: bool = False`。Agent 的 `search_docs` 工具调用时传 `use_rerank=True`。

### 3.6 文件

| 文件 | 改动 |
|------|------|
| `backend/reranker/base.py` | 新建：`BaseReranker` 抽象类 |
| `backend/reranker/cross_encoder.py` | 新建：Cross-Encoder 实现 |
| `backend/reranker/factory.py` | 新建：`create_reranker()` |
| `backend/config.py` | 新增 3 个 reranker 配置项 |
| `backend/rag/retriever.py` | `hybrid_search()` 接入去重 + Reranker |
| `backend/agent/tools.py` | `search_docs` 传 `use_rerank=True` |

---

## 4. 依赖

```
pip install sentence-transformers
```
