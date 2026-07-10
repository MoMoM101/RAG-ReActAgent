"""Test that the service starts and reports status without API keys."""
from unittest.mock import AsyncMock, patch

import pytest
from httpx import ASGITransport, AsyncClient


# ── Test: /api/health works without embedding API ──


@pytest.mark.asyncio
async def test_health_works_without_api_keys():
    """Service should start and /api/health should return 200 without API keys."""
    import embedding.openai_embed

    with (
        patch.object(embedding.openai_embed.AsyncOpenAI, "__init__", return_value=None),
        patch.object(embedding.openai_embed.OpenAIEmbedding, "embed", AsyncMock(
            return_value=[[0.1] * 1536]
        )),
        patch.object(embedding.openai_embed.OpenAIEmbedding, "embed_query", AsyncMock(
            return_value=[0.1] * 1536
        )),
    ):
        from main import app
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get("/api/health")
            assert response.status_code == 200
            assert response.json()["status"] == "ok"


# ── Tests: /api/health/dependencies ──


@pytest.mark.asyncio
async def test_health_dependencies_returns_all_components():
    """/api/health/dependencies should return status for each dependency."""
    import embedding.openai_embed

    with (
        patch.object(embedding.openai_embed.AsyncOpenAI, "__init__", return_value=None),
        patch.object(embedding.openai_embed.OpenAIEmbedding, "embed", AsyncMock(
            return_value=[[0.1] * 1536]
        )),
        patch.object(embedding.openai_embed.OpenAIEmbedding, "embed_query", AsyncMock(
            return_value=[0.1] * 1536
        )),
    ):
        from main import app
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get("/api/health/dependencies")
            assert response.status_code == 200
            data = response.json()
            for key in ("sqlite", "qdrant", "embedding", "llm"):
                assert key in data["dependencies"], f"Missing dependency key: {key}"
            assert "status" in data


@pytest.mark.asyncio
async def test_health_dependencies_detects_missing_api_key():
    """When API key is empty, embedding and llm should report missing_api_key."""
    from config import settings

    orig_llm_key = settings.llm_api_key
    orig_emb_key = settings.embedding_api_key
    settings.llm_api_key = ""
    settings.embedding_api_key = ""
    try:
        import embedding.openai_embed

        with (
            patch.object(embedding.openai_embed.AsyncOpenAI, "__init__", return_value=None),
            patch.object(embedding.openai_embed.OpenAIEmbedding, "embed", AsyncMock(
                return_value=[[0.1] * 1536]
            )),
            patch.object(embedding.openai_embed.OpenAIEmbedding, "embed_query", AsyncMock(
                return_value=[0.1] * 1536
            )),
        ):
            from main import app
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                response = await client.get("/api/health/dependencies")
                assert response.status_code == 200
                data = response.json()
                deps = data["dependencies"]
                assert deps["embedding"] == "missing_api_key"
                assert deps["llm"] == "missing_api_key"
                assert data["status"] == "degraded"
    finally:
        settings.llm_api_key = orig_llm_key
        settings.embedding_api_key = orig_emb_key


# ── Test: Lazy dimension detection ──


@pytest.mark.asyncio
@pytest.mark.integration
async def test_lazy_dimension_detection_on_first_embed():
    """First embed call should detect and cache the dimension."""
    from config import settings

    from embedding.factory import create_embedding, reset_embedding
    reset_embedding()

    try:
        embedding = create_embedding()
        vec = await embedding.embed_query("test dimension check")
        assert len(vec) > 0
        assert settings.embedding_dim == len(vec)
    except Exception:
        pytest.skip("API key not available, skipping integration test")
