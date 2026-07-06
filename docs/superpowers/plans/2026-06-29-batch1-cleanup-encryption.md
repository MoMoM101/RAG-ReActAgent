# 批次 1：代码清理 + API Key 加密 — 实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 删除 Agent Loop 未使用的 `chat()` 方法，将 API Key 加密集成到 settings 读写流程。

**Architecture:** 两个独立改动，互不依赖。Task 1-4 清理 LLM 层的冗余方法，Task 5-8 将已有的 `utils/crypto.py` 接入 config 和 settings API。

**Tech Stack:** Python, FastAPI, pytest, cryptography

---

### Task 1: 添加流式结果收集辅助函数 + 更新 `test_simple_chat`

**Files:**
- Modify: `backend/tests/llm/test_openai_llm.py`

- [ ] **Step 1: 添加辅助函数并改写 `test_simple_chat`**

在文件顶部（import 之后、`_api_key_valid` 之前）添加辅助函数：

```python
async def _collect_stream(llm, messages, tools=None):
    """将 chat_stream 所有 chunk 收集为一个 LLMResponse。"""
    content_parts = []
    tool_calls = []
    async for chunk in llm.chat_stream(messages, tools=tools):
        if chunk.content:
            content_parts.append(chunk.content)
        if chunk.tool_calls:
            tool_calls = chunk.tool_calls
    return LLMResponse(
        content="".join(content_parts) if content_parts else None,
        tool_calls=tool_calls,
        is_final=not bool(tool_calls),
    )
```

将 `test_simple_chat`（第 24-35 行）改为：

```python
@requires_api_key
@pytest.mark.asyncio
async def test_simple_chat():
    """A basic chat completion without tools should return content."""
    llm = _make_llm()
    messages = [
        ChatMessage(role="user", content="Say exactly 'hello world' with no other text.")
    ]
    response = await _collect_stream(llm, messages)
    assert isinstance(response, LLMResponse)
    assert response.content is not None
    assert "hello" in response.content.lower()
    assert response.is_final is True
    assert response.tool_calls == []
```

- [ ] **Step 2: 提交**

```bash
git add backend/tests/llm/test_openai_llm.py
git commit -m "refactor: replace chat() with _collect_stream helper in test_simple_chat"
```

---

### Task 2: 改写 `test_function_calling` 和 `test_chat_with_system_message`

**Files:**
- Modify: `backend/tests/llm/test_openai_llm.py`

- [ ] **Step 1: 改写 `test_function_calling`**

将第 88 行的 `await llm.chat(messages, tools=tools)` 改为：

```python
    response = await _collect_stream(llm, messages, tools=tools)
```

- [ ] **Step 2: 改写 `test_chat_with_system_message`**

将第 106 行的 `await llm.chat(messages)` 改为：

```python
    response = await _collect_stream(llm, messages)
```

- [ ] **Step 3: 提交**

```bash
git add backend/tests/llm/test_openai_llm.py
git commit -m "refactor: replace remaining chat() calls with _collect_stream in tests"
```

---

### Task 3: 删除 `chat()` 方法

**Files:**
- Modify: `backend/llm/base.py`
- Modify: `backend/llm/openai_llm.py`

- [ ] **Step 1: 删除基类中的 `chat()` 抽象方法**

在 `llm/base.py` 中删除第 30-35 行：

```python
    @abstractmethod
    async def chat(
        self,
        messages: list[ChatMessage],
        tools: list[dict] | None = None,
    ) -> LLMResponse: ...
```

注意保留 `chat_stream` 不动。删除后 `BaseLLM` 类变为：

```python
class BaseLLM(ABC):
    @abstractmethod
    async def chat_stream(
        self,
        messages: list[ChatMessage],
        tools: list[dict] | None = None,
    ):
        """Async generator yielding LLMResponse chunks for streaming"""
        ...
```

- [ ] **Step 2: 删除 `OpenAILLM` 中的 `chat()` 实现**

在 `llm/openai_llm.py` 中删除第 40-62 行（整个 `chat` 方法），保留 `_build_messages` 和 `chat_stream` 不动。

- [ ] **Step 3: 提交**

```bash
git add backend/llm/base.py backend/llm/openai_llm.py
git commit -m "refactor: remove unused chat() method, keep only chat_stream()"
```

---

### Task 4: 运行测试验证

**Files:**
- （无改动，仅验证）

- [ ] **Step 1: 运行 LLM 测试**

```bash
cd backend && python -m pytest tests/llm/test_openai_llm.py -v
```

预期：4 个测试全部 PASS（需要有效的 API Key）。

如果没有 API Key（3 个测试 skip），至少确认没有 import 错误或语法错误：

```bash
cd backend && python -c "from llm.openai_llm import OpenAILLM; print('OK')"
```

- [ ] **Step 2: 提交**

如果一切通过，无需额外提交。如果修改了测试文件，amend 到对应 commit。

---

### Task 5: `config.py` — 启动时自动解密 + 自动生成 secret_key

**Files:**
- Modify: `backend/config.py`

- [ ] **Step 1: 修改 `config.py`**

在 `config.py` 末尾，`settings = Settings()` 之前添加启动处理逻辑：

```python
from pydantic_settings import BaseSettings
import os
import secrets
from pathlib import Path


class Settings(BaseSettings):
    # ... (所有现有字段保持不变)
    model_config = {"env_file": ".env", "env_file_encoding": "utf-8"}


def _init_settings() -> Settings:
    """Load settings and handle encryption + secret_key auto-generation."""
    s = Settings()

    env_path = Path(s.model_config.get("env_file", ".env"))

    # Auto-generate secret_key if still default
    if s.secret_key == "change-me-in-production":
        s.secret_key = secrets.token_urlsafe(32)
        _write_env_key(env_path, "SECRET_KEY", s.secret_key)

    # Decrypt API keys if encrypted
    from utils.crypto import decrypt_if_needed
    if s.llm_api_key:
        s.llm_api_key = decrypt_if_needed(s.llm_api_key, s.secret_key)
    if s.embedding_api_key:
        s.embedding_api_key = decrypt_if_needed(s.embedding_api_key, s.secret_key)

    return s


def _write_env_key(env_path: Path, key: str, value: str) -> None:
    """Write or update a single key in .env file."""
    if env_path.exists():
        lines = env_path.read_text(encoding="utf-8").splitlines()
        new_lines = []
        found = False
        for line in lines:
            if line.strip().startswith(f"{key}=") or line.strip().startswith(f"{key} "):
                new_lines.append(f"{key}={value}")
                found = True
            else:
                new_lines.append(line)
        if not found:
            new_lines.append(f"{key}={value}")
        env_path.write_text("\n".join(new_lines) + "\n", encoding="utf-8")
    else:
        env_path.write_text(f"{key}={value}\n", encoding="utf-8")


settings = _init_settings()
```

- [ ] **Step 2: 验证启动**

```bash
cd backend && python -c "from config import settings; print('secret_key:', settings.secret_key[:8] + '***'); print('OK')"
```

预期：输出 secret_key 前缀 + OK，且 `.env` 中 `SECRET_KEY` 已更新为非默认值。

- [ ] **Step 3: 提交**

```bash
git add backend/config.py
git commit -m "feat: auto-decrypt API keys on startup + auto-generate secret_key"
```

---

### Task 6: `api/settings.py` — 保存时自动加密 API Key

**Files:**
- Modify: `backend/api/settings.py`

- [ ] **Step 1: 修改 `update_settings` 函数**

在 `update_settings` 中，API Key 写入 `.env` 前先加密。修改第 111-132 行：

```python
@router.put("")
async def update_settings(body: SettingsResponse):
    from utils.crypto import encrypt_if_needed

    env_updates = {
        "LLM_PROVIDER": body.llm.provider,
        "LLM_MODEL": body.llm.model,
        "LLM_BASE_URL": body.llm.base_url,
        "EMBEDDING_PROVIDER": body.embedding.provider,
        "EMBEDDING_MODEL": body.embedding.model,
        "EMBEDDING_BASE_URL": body.embedding.base_url,
    }
    # Encrypt and only update api_key if a real new one is provided (not masked)
    if body.llm.api_key and "***" not in body.llm.api_key:
        env_updates["LLM_API_KEY"] = encrypt_if_needed(body.llm.api_key, settings.secret_key)
    if body.embedding.api_key and "***" not in body.embedding.api_key:
        env_updates["EMBEDDING_API_KEY"] = encrypt_if_needed(body.embedding.api_key, settings.secret_key)

    _write_env(env_updates)
    _write_env({
        **env_updates,
        "WEB_SEARCH_ENABLED": str(body.web_search_enabled).lower(),
        "RERANK_ENABLED": str(body.rerank_enabled).lower(),
    })

    # Reload settings from updated .env
    from config import Settings, _init_settings
    global settings
    new_settings = _init_settings()
    for field in settings.model_fields:
        setattr(settings, field, getattr(new_settings, field))

    return {"status": "saved"}
```

- [ ] **Step 2: 验证加解密流程**

```bash
cd backend && python -c "
from utils.crypto import encrypt_if_needed, decrypt_if_needed
key = 'sk-test-12345'
secret = 'my-secret'
enc = encrypt_if_needed(key, secret)
print('Encrypted:', enc[:20] + '...')
dec = decrypt_if_needed(enc, secret)
print('Decrypted:', dec)
assert dec == key
print('OK')
"
```

预期：输出 Encrypted 前缀 + Decrypted 原始值 + OK。

- [ ] **Step 3: 提交**

```bash
git add backend/api/settings.py
git commit -m "feat: encrypt API keys on settings save"
```

---

### Task 7: 端到端验证

**Files:**
- （无改动，仅验证）

- [ ] **Step 1: 测试完整流程**

```bash
cd backend && python -c "
from config import settings

# 1. secret_key 不再是默认值
assert settings.secret_key != 'change-me-in-production', 'secret_key not generated'

# 2. 模拟加密后的回读
from utils.crypto import encrypt_if_needed, decrypt_if_needed
test_key = 'sk-test-1234567890abcdef'

# 加密
enc = encrypt_if_needed(test_key, settings.secret_key)
assert enc.startswith('ENC:'), f'Expected ENC: prefix, got {enc[:10]}'

# 解密
dec = decrypt_if_needed(enc, settings.secret_key)
assert dec == test_key, f'Roundtrip failed: {dec} != {test_key}'

print('All assertions passed')
"
```

- [ ] **Step 2: 确认无 import 错误**

```bash
cd backend && python -c "from config import settings; from api.settings import router; print('Import OK')"
```

- [ ] **Step 3: 提交**

如果一切通过，无需额外提交。

---

### Task 8: 更新 TEST_CHECKLIST.md

**Files:**
- Modify: `TEST_CHECKLIST.md`

- [ ] **Step 1: 更新测试清单**

在 `TEST_CHECKLIST.md` 末尾添加：

```markdown
## 批次 1 验证 (2026-06-29)
- [ ] `python -c "from config import settings"` 无 import 错误
- [ ] `python -m pytest tests/llm/test_openai_llm.py -v` 全部通过（有 API Key 时）
- [ ] `.env` 中 `SECRET_KEY` 已自动生成（非 change-me-in-production）
- [ ] 前端设置页保存 API Key 后，`.env` 中对应字段为 `ENC:` 开头
- [ ] 重启后端后 API Key 可正常使用（解密成功）
```

- [ ] **Step 2: 提交**

```bash
git add TEST_CHECKLIST.md
git commit -m "docs: add batch 1 verification checklist"
```
