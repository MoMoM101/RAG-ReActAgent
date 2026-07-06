from openai import AsyncOpenAI
from config import settings
from .base import BaseEmbedding

# Some providers limit batch size (Qwen v4: 10, v3: 20, others vary)
BATCH_SIZE = 10


class OpenAIEmbedding(BaseEmbedding):
    def __init__(self):
        self.client = AsyncOpenAI(
            api_key=settings.embedding_api_key or settings.llm_api_key,
            base_url=settings.embedding_base_url or settings.llm_base_url,
        )
        self.model = settings.embedding_model

    async def embed(self, texts: list[str]) -> list[list[float]]:
        results: list[list[float]] = []
        for i in range(0, len(texts), BATCH_SIZE):
            batch = texts[i:i + BATCH_SIZE]
            response = await self.client.embeddings.create(
                model=self.model,
                input=batch,
            )
            results.extend(d.embedding for d in response.data)
        return results

    async def embed_query(self, text: str) -> list[float]:
        results = await self.embed([text])
        return results[0]
