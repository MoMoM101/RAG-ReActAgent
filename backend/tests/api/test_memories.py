from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException
from sqlalchemy import func, select

from api import memories
from api.memories import UpdateMemoryRequest
from models.database import session_scope
from models.orm import UserMemory


def test_flatten_profile_covers_all_memory_types():
    timestamp = datetime(2026, 7, 20, tzinfo=UTC).isoformat()
    items = memories._flatten_profile(
        {
            "name": "Alice",
            "role": "Architect",
            "preferences": ["偏好简洁回答"],
            "decisions": ["使用 SQLite"],
            "facts": [{"content": "项目代号星河", "ts": timestamp}, "普通事实"],
        }
    )

    assert [item["id"] for item in items] == [
        "name:0",
        "role:0",
        "preference:0",
        "decision:0",
        "fact:0",
        "fact:1",
    ]
    assert [item["memory_type"] for item in items] == [
        "identity",
        "identity",
        "preference",
        "decision",
        "fact",
        "fact",
    ]
    assert items[4]["created_at"] == timestamp


@pytest.mark.asyncio
async def test_list_memories_filters_by_type():
    profile = {
        "name": "Alice",
        "preferences": ["偏好简洁回答"],
        "facts": ["项目代号星河"],
    }
    with patch("memory.profile.get_profile", AsyncMock(return_value=profile)):
        result = await memories.list_memories(memory_type="fact")

    assert [item["content"] for item in result["memories"]] == ["项目代号星河"]


@pytest.mark.asyncio
async def test_get_profile_delegates_to_profile_store():
    profile = {"name": "Alice"}
    with patch("memory.profile.get_profile", AsyncMock(return_value=profile)) as get:
        assert await memories.get_profile() == profile
    get.assert_awaited_once_with()


@pytest.mark.asyncio
async def test_update_and_delete_memory_success():
    with (
        patch("memory.profile.update_item", AsyncMock(return_value={"name": "Alice"})) as update,
        patch("memory.profile.delete_item", AsyncMock(return_value={"name": ""})) as delete,
    ):
        updated = await memories.update_memory("name:0", UpdateMemoryRequest(content="Alice"))
        deleted = await memories.delete_memory("name:0")

    assert updated == {"content": "Alice"}
    assert deleted == {"status": "deleted"}
    update.assert_awaited_once_with("name:0", "Alice")
    delete.assert_awaited_once_with("name:0")


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["update", "delete"])
async def test_missing_memory_returns_404(operation):
    target = "memory.profile.update_item" if operation == "update" else "memory.profile.delete_item"
    with (
        patch(target, AsyncMock(return_value=None)),
        pytest.raises(HTTPException) as exc,
    ):
        if operation == "update":
            await memories.update_memory("fact:99", UpdateMemoryRequest(content="missing"))
        else:
            await memories.delete_memory("fact:99")
    assert exc.value.status_code == 404


@pytest.mark.asyncio
async def test_clear_all_memories_deletes_sqlite_vectors_and_profile():
    async with session_scope() as session:
        session.add_all(
            [
                UserMemory(id="memory-1", content="one", memory_type="fact"),
                UserMemory(id="memory-2", content="two", memory_type="fact"),
            ]
        )
        await session.commit()

    vector_db = MagicMock()
    vector_db.delete_by_ids = AsyncMock()
    save = AsyncMock()
    with (
        patch("vectordb.qdrant.QdrantVectorDB", return_value=vector_db),
        patch("memory.profile._save", save),
    ):
        result = await memories.clear_all_memories()

    assert result == {"status": "cleared", "count": 2}
    vector_db.delete_by_ids.assert_awaited_once_with(["memory-1", "memory-2"])
    save.assert_awaited_once()
    async with session_scope() as session:
        count = await session.scalar(select(func.count(UserMemory.id)))
    assert count == 0


@pytest.mark.asyncio
async def test_clear_empty_memories_skips_vector_database():
    with (
        patch("vectordb.qdrant.QdrantVectorDB") as vector_db,
        patch("memory.profile._save", AsyncMock()),
    ):
        result = await memories.clear_all_memories()

    assert result == {"status": "cleared", "count": 0}
    vector_db.assert_not_called()
