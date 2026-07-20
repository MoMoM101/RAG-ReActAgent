"""Connection probe implementation for the settings API."""

import time

import httpx
from openai import AsyncOpenAI

from api.settings_models import TestConnectionRequest
from config import settings


async def test_connection(req: TestConnectionRequest):
    """Test LLM or embedding connectivity with provided config."""
    if req.kind == "embedding":
        api_key = (
            req.api_key
            if (req.api_key and "***" not in req.api_key)
            else (settings.embedding_api_key or settings.llm_api_key)
        )
        base_url = req.base_url or settings.embedding_base_url or settings.llm_base_url
        model = req.model or settings.embedding_model
    else:
        api_key = (
            req.api_key
            if (req.api_key and "***" not in req.api_key)
            else settings.llm_api_key
        )
        base_url = req.base_url or settings.llm_base_url
        model = req.model or settings.llm_model

    started = time.time()
    http_client = httpx.AsyncClient(proxy=None, trust_env=False)
    client = AsyncOpenAI(api_key=api_key, base_url=base_url, http_client=http_client)

    if req.kind == "embedding":
        try:
            embedding_response = await client.embeddings.create(model=model, input=["hello"])
            latency_ms = int((time.time() - started) * 1000)
            dimension = (
                len(embedding_response.data[0].embedding)
                if embedding_response.data
                else 0
            )
            return {"ok": True, "latency_ms": latency_ms, "detail": f"dim={dimension}"}
        except Exception as error:
            return {
                "ok": False,
                "latency_ms": int((time.time() - started) * 1000),
                "detail": str(error)[:300],
            }

    try:
        chat_response = await client.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": "hi"}],
            max_tokens=5,
        )
        latency_ms = int((time.time() - started) * 1000)
        reply = (
            (chat_response.choices[0].message.content or "").strip()
            if chat_response.choices
            else ""
        )
        return {"ok": True, "latency_ms": latency_ms, "detail": reply}
    except Exception as error:
        return {
            "ok": False,
            "latency_ms": int((time.time() - started) * 1000),
            "detail": str(error)[:300],
        }
