# P1 修复 — OCR 预处理接入 + Loader/Retriever 集成测试

**日期**: 2026-07-02 | **优先级**: P1 | **预计改动范围**: 3 文件改动 + 2 文件新增

评审报告来源：负面评价 3.5（OCR 未接入）、3.7（缺少集成测试）

---

## 问题背景

- **OCR**：`preprocess.py` 47 行代码（去噪 → CLAHE → 自适应二值化）闲置，`loaders.py` 直接将原始像素传给 OCR，识别率未达最优
- **Loader 测试**：PDF / DOCX / XLSX / 图片四种格式零测试
- **Retriever 测试**：`hybrid_search()` 全链路从未被验证，8 个现有测试全是 `_rrf_fusion` 纯函数测试

---

## Part A — OCR 预处理接入

### 降级策略：三级级联

```
预处理 → 成功 → OCR识别 → 成功 → 增强后文本 ✓
                      ↘ 失败 → logger.warning, 跳过
        ↘ 失败 → 原始像素OCR → 成功 → 和现在一样的文本 ✓
                            ↘ 失败 → logger.warning, 跳过
```

每处改动从单层 try 改为嵌套 try：

```python
# 旧代码
try:
    ocr_text = ocr.recognize_from_bytes(img_bytes, dpi=200)
except Exception:
    pass

# 新代码
try:
    from ocr.preprocess import image_from_bytes
    preprocessed = image_from_bytes(img_bytes, dpi=200)
    ocr_text = ocr.recognize(preprocessed)
except Exception:
    try:
        ocr_text = ocr.recognize_from_bytes(img_bytes, dpi=200)
    except Exception:
        logger.warning("OCR failed both preprocess and raw, image size=%d", len(img_bytes))
```

### 改动点

| 文件 | 位置 | 改动 |
|------|------|------|
| `backend/rag/loaders.py` | 头部 | 新增 `import logging` + `logger` |
| `load_pdf()` | L26 内嵌图片 | 单层 try → 嵌套 try + 降级 + warning |
| `load_pdf()` | L41 整页 OCR | 同上 |
| `load_image()` | L89 | 同上 |

预处理管线效果：

```
原始像素 → DPI提升 → 灰度化 → 降噪 → CLAHE增强 → 二值化 → 干净二值图 → OCR
```

两端都失败时才打 warning（图像损坏、格式异常等边缘情况），用户无感知——loader 行为不变，只是多了一条可追溯的日志。

---

## Part B — Loader 集成测试

新增 `tests/rag/test_loaders_integration.py`：

| 测试 | 内容 | 依赖 |
|------|------|------|
| `test_load_pdf_text` | 创建含文本的 PDF，验证 `load_pdf()` 提取正确 | fpdf2（创建 PDF） |
| `test_load_docx` | 创建含段落的 DOCX，验证 `load_docx()` 提取正确 | python-docx（已有） |
| `test_load_xlsx` | 创建含行列的 XLSX，验证 `load_xlsx()` 提取 markdown | openpyxl（已有） |
| `test_load_image_ocr_disabled` | OCR 关闭时返回空字符串 | 无需 OCR |
| `test_load_document_routing_all` | 验证 LOADERS 路由 8 种格式 | tmp_path |

测试模式沿用现有 `test_loaders.py` —— `tmp_path` 创建临时文件，直接调 loader 函数。

## Part C — Retriever 集成测试

新增 `tests/rag/test_retriever_integration.py`：

`hybrid_search()` 测试策略：

| 层 | 测试 | 方式 |
|------|------|------|
| 语义检索 | 向 Qdrant 写入测试数据，search 验证命中 | 真实 Qdrant |
| 混合检索 | 同时写入 Qdrant + FTS5，验证 RRF 融合结果 | 真实 Qdrant + FTS5 |
| 去重 | 写入两文档含相似 chunk，验证去重生效 | `_dedup_results` 测试 |
| 日志 | 验证 search 汇总日志格式正确 | 现有日志系统 |

Qdrant 不可用时自动 skip（沿用现有 test_qdrant.py 模式）。FTS5 用测试库独立运行。

FTS5 集成测试使用临时数据库，不影响生产数据。

---

## 影响范围

| 文件 | 改动 | 说明 |
|------|------|------|
| `backend/rag/loaders.py` | 3 处 `recognize_from_bytes` → `image_from_bytes` + `recognize` | OCR 预处理接入 |
| `backend/tests/rag/test_loaders_integration.py` | **新建** | 5 个 Loader 集成测试 |
| `backend/tests/rag/test_retriever_integration.py` | **新建** | 5 个 Retriever 集成测试 |
| `backend/tests/rag/test_retriever.py` | 新增 3 个 | `_dedup_results` 单元测试 |

无 API 变更，无前端变更，无数据库 schema 变更。

---

## 验证方式

1. 上传低分辨率中文扫描件 → 验证 OCR 输出文字比之前多
2. `pytest tests/rag/ -v` → 确认新增测试通过
3. `pytest backend/tests/ -v` → 确认 89 个旧测试无回归
