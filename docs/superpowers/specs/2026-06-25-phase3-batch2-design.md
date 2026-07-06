# 三期第二批 — Web Search + OCR 设计文档

**日期**: 2026-06-25 | **状态**: 已确认

---

## 1. Web Search

### 1.1 方案

DuckDuckGo 免费搜索 + 可配 HTTP 代理应对国内网络限制。

### 1.2 接入

新增 `WebSearchTool`，继承 `BaseTool`，注册到 `ToolRegistry`：

- **触发**: Agent 自主判断——知识库检索结果不足时调用 `web_search`
- **system prompt**: 加引导："知识库信息不足时，可调用 web_search 从互联网搜索"
- **classifier**: 加关键词 "网上搜"/"搜索一下"/"查一下网上"

### 1.3 依赖

```
pip install duckduckgo-search
```

### 1.4 配置 (.env)

```
WEB_SEARCH_ENABLED=true
WEB_SEARCH_MAX_RESULTS=5
WEB_SEARCH_PROXY=          # 可选，如 socks5://127.0.0.1:1080
```

### 1.5 返回格式

```json
{
  "count": 3,
  "results": [
    {"title": "内网穿透 - 百度百科", "snippet": "...", "url": "https://..."}
  ]
}
```

### 1.6 重试策略

`exponential`，max 2（网络请求，值得重试）

---

## 2. OCR

### 2.1 方案

PaddleOCR，百度开源，中文识别最强。

### 2.2 架构

新建 `backend/ocr/`，风格与 `reranker/` 一致：

```
backend/ocr/
├── base.py              # BaseOCR 抽象类
├── paddle_ocr.py        # PaddleOCR 实现
├── preprocess.py        # 图片预处理管线
├── factory.py           # create_ocr() 单例
```

### 2.3 预处理管线

OCR 前对图片做预处理，提升识别率：

```
原始图片
 → 灰度化 (cv2.COLOR_BGR2GRAY)
 → 降噪 (cv2.fastNlMeansDenoising)
 → 二值化 (cv2.adaptiveThreshold)
 → PaddleOCR 识别
```

- 扫描 PDF 页面：DPI ≥ 200，低于则放大
- 照片类图片：先做对比度增强再走预处理

### 2.4 接入点

`backend/rag/loaders.py`：

- **PDF**: PyMuPDF 提取文本 → 某页文本量 < 50 字 → 该页转图片走 OCR
- **图片 (jpg/png)**: 直接走 OCR
- **其他格式**: 不动

### 2.5 依赖

```
pip install paddleocr paddlepaddle opencv-python-headless
```

### 2.5 配置 (.env)

```
OCR_ENABLED=true
OCR_MIN_TEXT_LENGTH=50
```

---

## 3. 文件改动

| 文件 | 改动 | 模块 |
|------|------|------|
| `backend/agent/tools.py` | 新增 WebSearchTool | Web Search |
| `backend/agent/context.py` | system prompt 加 web_search 引导 | Web Search |
| `backend/agent/classifier.py` | 加 web_search 关键词 | Web Search |
| `backend/config.py` | 新增 web_search + ocr 配置项 | 两个 |
| `backend/ocr/base.py` | 新建 BaseOCR 抽象类 | OCR |
| `backend/ocr/paddle_ocr.py` | 新建 PaddleOCR 实现 | OCR |
| `backend/ocr/preprocess.py` | 新建图片预处理管线 | OCR |
| `backend/ocr/factory.py` | 新建 create_ocr() | OCR |
| `backend/rag/loaders.py` | PDF 缺文字页 + 图片走 OCR | OCR |
