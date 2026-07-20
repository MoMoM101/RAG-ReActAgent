import httpx
from openai import AsyncOpenAI

from config import settings

from .base import BaseEmbedding

# Some providers limit batch size (Qwen v4: 10, v3: 20, others vary)
BATCH_SIZE = 10


class OpenAIEmbedding(BaseEmbedding):
    def __init__(self):
        timeout = httpx.Timeout(
            connect=10.0,
            read=settings.embedding_timeout,
            write=30.0,
            pool=10.0,
        )
        http_client = httpx.AsyncClient(proxy=None, trust_env=False, timeout=timeout)
        self.client = AsyncOpenAI(
            api_key=settings.embedding_api_key or settings.llm_api_key,
            base_url=settings.embedding_base_url or settings.llm_base_url,
            http_client=http_client,
            max_retries=0,
        )
        self.model = settings.embedding_model

    async def embed(self, texts: list[str]) -> list[list[float]]:
        results: list[list[float]] = []
        total_tokens = 0
        for i in range(0, len(texts), BATCH_SIZE):
            batch = texts[i:i + BATCH_SIZE]
            response = await self.client.embeddings.create(
                model=self.model,
                input=batch,
            )
            results.extend(d.embedding for d in response.data)
            total_tokens += getattr(response, "usage", None) and response.usage.total_tokens or sum(len(t) // 3 for t in batch)

        try:
            from metrics import get_metrics
            get_metrics().record_embedding_usage(max(total_tokens, 1))
        except Exception:
            pass
        return results

    async def embed_query(self, text: str) -> list[float]:
        results = await self.embed([text])
        return results[0]
