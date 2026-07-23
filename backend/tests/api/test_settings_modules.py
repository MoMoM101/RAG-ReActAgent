from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from pydantic import ValidationError

import api.settings as facade
from api.settings_env import env_bool, mask_key, read_env, write_env
from api.settings_models import (
    EmbeddingSettings,
    LLMSettings,
    SettingsResponse,
)
from api.settings_models import (
    TestConnectionRequest as ConnectionRequest,
)


def test_settings_facade_preserves_public_symbols_and_route():
    assert facade.LLMSettings is LLMSettings
    assert facade.EmbeddingSettings is EmbeddingSettings
    assert facade.SettingsResponse is SettingsResponse
    assert facade.TestConnectionRequest is ConnectionRequest

    paths = [route.path for route in facade.router.routes]
    assert paths.count("/api/settings/test-connection") == 1


@pytest.mark.parametrize("model", [LLMSettings, EmbeddingSettings])
def test_provider_and_url_validation_remains_shared(model):
    with pytest.raises(ValidationError, match="Provider must be alphanumeric"):
        model(provider="Bad Provider")
    with pytest.raises(ValidationError, match="Base URL must start"):
        model(base_url="ftp://example.test")


def test_env_helpers_round_trip_and_preserve_existing_keys(tmp_path: Path):
    path = tmp_path / ".env"
    path.write_text("EXISTING=value\n", encoding="utf-8")

    write_env(path, {"PLAIN": "ok", "QUOTED": "two words"})

    content = path.read_text(encoding="utf-8")
    assert "EXISTING=value" in content
    assert "PLAIN=ok" in content
    assert 'QUOTED="two words"' in content
    assert read_env(path)["PLAIN"] == "ok"


def test_boolean_and_key_mask_helpers():
    assert env_bool({"FLAG": "YES"}, "FLAG", False) is True
    assert env_bool({"FLAG": "0"}, "FLAG", True) is False
    assert env_bool({}, "FLAG", True) is True
    assert mask_key("short") == "***"
    assert mask_key("1234567890") == "1234***7890"


@pytest.mark.asyncio
async def test_get_settings_reads_env_and_masks_secrets(monkeypatch):
    monkeypatch.setattr(
        facade,
        "_read_env",
        lambda: {
            "LLM_PROVIDER": "custom",
            "LLM_MODEL": "chat-model",
            "LLM_API_KEY": "1234567890",
            "LLM_BASE_URL": "https://llm.example/v1",
            "EMBEDDING_PROVIDER": "custom",
            "EMBEDDING_MODEL": "embed-model",
            "EMBEDDING_API_KEY": "abcdefghij",
            "EMBEDDING_BASE_URL": "https://embed.example/v1",
            "WEB_SEARCH_ENABLED": "false",
            "RERANK_ENABLED": "true",
            "RETRIEVAL_TOP_K": "9",
            "WEB_SEARCH_MAX_RESULTS": "7",
            "CHUNK_SIZE": "512",
            "CHUNK_OVERLAP": "64",
        },
    )

    result = await facade.get_settings()

    assert result.llm.model == "chat-model"
    assert result.llm.api_key == "1234***7890"
    assert result.embedding.api_key == "abcd***ghij"
    assert result.web_search_enabled is False
    assert result.rerank_enabled is True
    assert result.retrieval_top_k == 9
    assert result.chunk_size == 512


@pytest.mark.asyncio
async def test_update_settings_persists_keys_and_resets_empty_mismatch(monkeypatch):
    from config import settings

    for field in settings.model_fields:
        monkeypatch.setattr(settings, field, getattr(settings, field))

    updated_runtime = settings.model_copy(deep=True)
    updated_runtime.llm_provider = "custom"
    updated_runtime.llm_model = "chat-model"
    updated_runtime.llm_base_url = "https://llm.example/v1"
    updated_runtime.embedding_provider = "custom"
    updated_runtime.embedding_model = "embed-model"
    updated_runtime.embedding_base_url = "https://embed.example/v1"

    write = MagicMock()
    detect = AsyncMock(
        side_effect=[
            {
                "ok": True,
                "mismatch": True,
                "document_count": 0,
                "current_model_dim": 768,
            },
            {
                "ok": True,
                "mismatch": False,
                "document_count": 0,
                "current_model_dim": 768,
            },
        ]
    )
    reset = AsyncMock()
    monkeypatch.setattr(facade, "_write_env", write)
    monkeypatch.setattr(facade, "_detect_dimension_mismatch", detect)
    monkeypatch.setattr(facade, "_reset_qdrant_collections", reset)
    monkeypatch.setattr("config._init_settings", lambda: updated_runtime)
    monkeypatch.setattr(
        "utils.crypto.encrypt_if_needed",
        lambda value, _secret: f"encrypted:{value}",
    )

    body = SettingsResponse(
        llm=LLMSettings(
            provider="custom",
            model="chat-model",
            api_key="new-llm-key",
            base_url="https://llm.example/v1",
        ),
        embedding=EmbeddingSettings(
            provider="custom",
            model="embed-model",
            api_key="new-embedding-key",
            base_url="https://embed.example/v1",
        ),
        web_search_enabled=False,
        rerank_enabled=True,
        retrieval_top_k=8,
        web_search_max_results=6,
        chunk_size=512,
        chunk_overlap=64,
    )

    result = await facade.update_settings(body)

    persisted = write.call_args.args[0]
    assert persisted["LLM_API_KEY"] == "encrypted:new-llm-key"
    assert persisted["EMBEDDING_API_KEY"] == "encrypted:new-embedding-key"
    assert persisted["CHUNK_SIZE"] == "512"
    reset.assert_awaited_once_with(768)
    assert detect.await_count == 2
    assert result["dimension"]["mismatch"] is False


@pytest.mark.asyncio
async def test_dimension_check_contains_embedding_failure(monkeypatch):
    chunks = MagicMock()
    chunks.get_collection_dim = AsyncMock(return_value=None)
    profile = MagicMock()
    profile.get_collection_dim = AsyncMock(return_value=1536)

    clients = iter([chunks, profile])
    monkeypatch.setattr(
        "vectordb.qdrant.QdrantVectorDB",
        lambda *args, **kwargs: next(clients),
    )
    with patch(
        "openai.AsyncOpenAI",
        side_effect=RuntimeError("embedding unavailable"),
    ):
        result = await facade.check_dimension()

    assert result["ok"] is False
    assert result["profile_dim"] == 1536
    assert "embedding unavailable" in result["error"]


@pytest.mark.asyncio
async def test_rebuild_status_reports_idle_live_and_final(monkeypatch):
    from rag.progress import progress

    monkeypatch.setattr(facade, "_rebuild_lock", False)
    monkeypatch.setattr(facade, "_last_rebuild_result", None)
    assert (await facade.rebuild_status())["status"] == "idle"

    monkeypatch.setattr(facade, "_rebuild_lock", True)
    progress._latest["rebuild"] = {"status": "rebuilding", "current": 2}
    assert await facade.rebuild_status() == {"status": "rebuilding", "current": 2}

    monkeypatch.setattr(facade, "_rebuild_lock", False)
    monkeypatch.setattr(facade, "_last_rebuild_result", {"status": "completed"})
    assert await facade.rebuild_status() == {"status": "completed"}
    progress._latest.pop("rebuild", None)


@pytest.mark.asyncio
async def test_rebuild_rejects_when_already_running(monkeypatch):
    monkeypatch.setattr(facade, "_rebuild_lock", True)
    assert await facade.rebuild_collections() == {
        "status": "rejected",
        "reason": "重建正在进行中，请等待完成后再试",
    }


@pytest.mark.asyncio
async def test_embedding_connection_probe_reports_dimension():
    client = MagicMock()
    client.embeddings.create = AsyncMock(return_value=SimpleNamespace(data=[SimpleNamespace(embedding=[0.1, 0.2, 0.3])]))
    with (
        patch("api.settings_connection.httpx.AsyncClient"),
        patch("api.settings_connection.AsyncOpenAI", return_value=client),
    ):
        result = await facade.test_connection(ConnectionRequest(kind="embedding", api_key="test-key"))

    assert result["ok"] is True
    assert result["detail"] == "dim=3"


@pytest.mark.asyncio
async def test_llm_connection_probe_returns_reply():
    client = MagicMock()
    client.chat.completions.create = AsyncMock(
        return_value=SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content="pong"))])
    )
    with (
        patch("api.settings_connection.httpx.AsyncClient"),
        patch("api.settings_connection.AsyncOpenAI", return_value=client),
    ):
        result = await facade.test_connection(ConnectionRequest(kind="llm", api_key="test-key"))

    assert result["ok"] is True
    assert result["detail"] == "pong"


@pytest.mark.asyncio
async def test_connection_probe_contains_provider_failure():
    client = MagicMock()
    client.chat.completions.create = AsyncMock(side_effect=RuntimeError("provider down"))
    with (
        patch("api.settings_connection.httpx.AsyncClient"),
        patch("api.settings_connection.AsyncOpenAI", return_value=client),
    ):
        result = await facade.test_connection(ConnectionRequest(kind="llm", api_key="test-key"))

    assert result["ok"] is False
    assert result["detail"] == "provider down"
