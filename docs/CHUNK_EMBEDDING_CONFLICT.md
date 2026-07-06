# 文档切块与 Embedding 模型切换的冲突分析

> 创建日期：2026-07-02  
> 状态：待设计修复方案

---

## 问题描述

`rebuild-collections` 从 FTS5 回读旧 chunk 文本重新生成向量，但换模型后**只换向量维度，不重新切分原始文档**。当新 embedding 模型的 `max_input` 小于旧 chunk 大小时，嵌入 API 直接报错，rebuild 全挂。

---

## 冲突场景复现

```
原始状态: text-embedding-3-small (dim=1536, max_input=8191), chunk_size=512
          → 文档切成 512 token 的块 → 生成 1536 维向量 → Qdrant+FTS5 正常

用户操作:
1. 切换到 bge-large-zh (dim=1024, max_input=512)
2. chunk_size 没改 (仍是 512)
3. 点 rebuild

rebuild 流程:
  1. 检测新维度 = 1024 ✓
  2. 重建 Qdrant collection dim=1024 ✓
  3. 从 FTS5 读出旧 chunk（每个 512 token）✓
  4. 用 bge-large-zh 给每个 chunk 生成 1024 维向量
     → API 报错: input too long (512 > 512，刚好卡在边界也很危险) ✗
```

更坏的场景：

```
chunk_size=2000, 模型 max_input=512
  → 每个 chunk 都超限，全部嵌入失败，0 个 chunk 写入 Qdrant
  → FTS5 在 step 4.5 还被清空重建
  → 最终：知识库变成空的
```

---

## 涉及组件

| 组件 | 当前行为 | 问题 |
|------|---------|------|
| `splitter.py` | 按 `settings.chunk_size` 切分 | 新上传的正常，旧数据不管 |
| `pipeline.py` `_process_document` | 切分 → embedding → Qdrant+FTS5 双写 | 不存原始文本 |
| `api/settings.py` `rebuild_collections` | FTS5 回读 → 换向量 → 写回 | **不重新切分，不校验模型上限** |
| `vectordb/factory.py` | 只检测维度，不检测 max_input | 不知道模型能处理多大的 chunk |

---

## 为什么日常搜索不受影响

搜索只做 `embed_query("用户问题")`，用户问题通常只有几十 token，远低于任意模型上限。不会触发 token 超限。

---

## 可能的修复方向

1. **简单**：rebuild 前校验 chunk 文本长度，超过模型上限则拒绝并提示用户"请减小 chunk_size 后重新上传文档"或"请重新上传文档（将按新 chunk_size 切分）"
2. **中等**：入库时将原始文本（`load_document()` 输出）存一份到 DB/文件，rebuild 时重新切分
3. **完整**：入库时保留原始文件不删，rebuild 时回读文件重新走 `load_document → split_text → embed → index` 全流程

方案 3 缺点：OCR 重跑很慢，文件可能已被删除。

---

## 关联 Bug 报告

- BUG_REPORT.md #7 splitter token 丢失
- BUG_REPORT.md #17 collection 已存在时不更新维度
