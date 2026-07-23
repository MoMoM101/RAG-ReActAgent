import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import memory.profile as profile


@pytest.mark.asyncio
async def test_profile_save_remains_durable_and_marks_index_dirty_on_failure(
    monkeypatch,
):
    embedding = MagicMock()
    embedding.embed = AsyncMock(side_effect=RuntimeError("embedding unavailable"))
    save = AsyncMock()
    monkeypatch.setattr(profile, "save_profile", save)
    monkeypatch.setattr(profile, "PROFILE_INDEX_DIRTY", False)

    with patch("embedding.factory.create_embedding", return_value=embedding):
        await profile._save({"name": "current user"})

    save.assert_awaited_once()
    assert profile.PROFILE_INDEX_DIRTY is True


@pytest.mark.asyncio
async def test_dirty_profile_index_bypasses_stale_qdrant(monkeypatch):
    vector_db = MagicMock()
    vector_db.collection_exists = AsyncMock(return_value=True)
    embedding = MagicMock()
    embedding.embed_query = AsyncMock(return_value=[1.0, 0.0])
    embedding.embed = AsyncMock(return_value=[[1.0, 0.0]])
    monkeypatch.setattr(profile, "PROFILE_INDEX_DIRTY", True)
    monkeypatch.setattr(profile, "_load", AsyncMock(return_value={"name": "current user"}))

    with (
        patch("vectordb.qdrant.QdrantVectorDB", return_value=vector_db),
        patch("embedding.factory.create_embedding", return_value=embedding),
    ):
        results = await profile.search_profile("current user")

    vector_db.collection_exists.assert_not_awaited()
    assert results[0]["text"] == "用户名叫current user"


@pytest.mark.asyncio
async def test_successful_empty_profile_cleanup_clears_dirty_flag(monkeypatch):
    monkeypatch.setattr(profile, "PROFILE_INDEX_DIRTY", True)
    monkeypatch.setattr(
        profile,
        "_cleanup_old_profile_collections",
        AsyncMock(return_value=True),
    )

    await profile._index_profile({})

    assert profile.PROFILE_INDEX_DIRTY is False


@pytest.mark.asyncio
async def test_concurrent_profile_writes_do_not_lose_updates(monkeypatch):
    monkeypatch.setattr(profile, "_is_similar", AsyncMock(return_value=False))
    monkeypatch.setattr(profile, "_index_profile", AsyncMock())

    await asyncio.gather(
        profile.append_list("preferences", "偏好简洁回答"),
        profile.append_list("preferences", "偏好中文回答"),
    )

    stored = await profile.get_profile()
    assert set(stored["preferences"]) == {"偏好简洁回答", "偏好中文回答"}
