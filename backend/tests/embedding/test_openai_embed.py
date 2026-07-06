import pytest

from config import settings
from embedding.openai_embed import OpenAIEmbedding


def _api_key_is_set() -> bool:
    """Check if an actual API key is configured (not the placeholder)."""
    key = settings.embedding_api_key or settings.llm_api_key
    if not key:
        return False
    if key == "your-api-key-here":
        return False
    return True


api_key_required = pytest.mark.skipif(
    not _api_key_is_set(),
    reason="No valid API key configured. Set EMBEDDING_API_KEY or LLM_API_KEY in .env.",
)


@pytest.mark.asyncio
@api_key_required
async def test_embed_single():
    emb = OpenAIEmbedding()
    result = await emb.embed(["测试文本"])
    assert len(result) == 1
    assert len(result[0]) > 0


@pytest.mark.asyncio
@api_key_required
async def test_embed_query():
    emb = OpenAIEmbedding()
    vec = await emb.embed_query("什么是部署流程")
    assert len(vec) > 0
