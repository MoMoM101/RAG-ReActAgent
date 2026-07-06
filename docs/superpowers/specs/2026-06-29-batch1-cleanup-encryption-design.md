# 批次 1：代码清理 + API Key 加密 — 设计文档

## #6 — 移除未使用的 `chat()` 方法

### 现状
- `llm/base.py:31` — `chat()` 抽象方法（非流式）
- `llm/openai_llm.py:40-62` — `chat()` 实现
- Agent Loop 只用 `chat_stream()`，从不调 `chat()`
- 唯一调用方：`tests/llm/test_openai_llm.py` 中的 3 个测试用例

### 改动
1. 删除 `llm/base.py` 中的 `chat()` 抽象方法
2. 删除 `llm/openai_llm.py` 中的 `chat()` 实现（40-62 行）
3. 更新 `tests/llm/test_openai_llm.py` — 3 个测试改为调用 `chat_stream()` + 收集结果

### 风险：低
无运行时调用、无 API 暴露、仅影响测试

---

## #5 — API Key 加密集成到 settings 读写流程

### 现状
- `utils/crypto.py` 已有 AES-256-GCM 加解密，用 `ENC:` 前缀标记密文
- `config.py` 有 `secret_key` 字段（默认值 `change-me-in-production`）
- `.env` 中 API Key 目前明文存储，加密工具未接入

### 改动

**config.py：**
- Settings 加载后，对 `llm_api_key` 和 `embedding_api_key` 调用 `decrypt_if_needed` 自动解密
- 若 `secret_key` 为默认值 `change-me-in-production`，自动生成随机密钥写入 `.env`

**api/settings.py：**
- `update_settings`（PUT）：API Key 写入 `.env` 前调用 `encrypt_if_needed` 加密
- `get_settings`（GET）：无需改动（已有 `_mask_key` 掩码显示）
- `test_connection`：无需改动（运行时内存中已是明文）

### 数据流
```
启动:  .env(ENC:xxx) → config 解密 → 内存中明文使用
保存:  用户输入明文 → settings API 加密 → .env(ENC:xxx)
读取:  .env(ENC:xxx) → 掩码显示 → 前端 ***
```

### 边界情况
- 现有明文 Key：下次保存设置时自动升级为加密格式
- secret_key 丢失：等同于 .env 文件丢失（明文 Key 也一样丢失）
- 首次启动自动生成 .env 模板：secret_key 同步生成，立即可用
