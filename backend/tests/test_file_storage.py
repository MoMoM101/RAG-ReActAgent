import os
import tempfile
import pytest
from storage.base import StagedObject, StoredObject
from storage.files import LocalFileStorage


@pytest.fixture
def storage():
    with tempfile.TemporaryDirectory() as tmp:
        yield LocalFileStorage(tmp)


@pytest.mark.asyncio
async def test_roundtrip_small_file(storage):
    staged = await storage.create_staging("test.txt")
    await storage.append(staged, b"hello")
    await storage.append(staged, b" world")
    stored = await storage.commit(staged)

    assert stored.size == 11
    assert stored.filename == "test.txt"

    chunks = [c async for c in storage.open_read(stored.storage_key)]
    assert b"".join(chunks) == b"hello world"


@pytest.mark.asyncio
async def test_hash_verification_rejects_mismatch(storage):
    staged = await storage.create_staging("data.bin")
    await storage.append(staged, b"secret")

    with pytest.raises(ValueError, match="Hash mismatch"):
        await storage.commit(staged, expected_sha256="00" * 32)


@pytest.mark.asyncio
async def test_hash_verification_accepts_match(storage):
    import hashlib
    staged = await storage.create_staging("data.bin")
    content = b"verify me"
    await storage.append(staged, content)
    expected = hashlib.sha256(content).hexdigest()
    stored = await storage.commit(staged, expected_sha256=expected)
    assert stored.sha256 == expected


@pytest.mark.asyncio
async def test_same_content_same_storage_key(storage):
    staged1 = await storage.create_staging("doc.pdf")
    await storage.append(staged1, b"pdf content")
    stored1 = await storage.commit(staged1)

    staged2 = await storage.create_staging("doc.pdf")
    await storage.append(staged2, b"pdf content")
    stored2 = await storage.commit(staged2)

    assert stored1.storage_key == stored2.storage_key


@pytest.mark.asyncio
async def test_path_traversal_blocked(storage):
    with pytest.raises(ValueError, match="Path traversal"):
        storage._safe_path("../../../etc/passwd")


@pytest.mark.asyncio
async def test_delete_idempotent(storage):
    # Deleting non-existent key should not raise
    await storage.delete("nonexistent/key")

    # Deleting a real key
    staged = await storage.create_staging("temp.txt")
    await storage.append(staged, b"x")
    stored = await storage.commit(staged)
    assert await storage.exists(stored.storage_key)
    await storage.delete(stored.storage_key)
    assert not await storage.exists(stored.storage_key)
    # Second delete is a no-op
    await storage.delete(stored.storage_key)


@pytest.mark.asyncio
async def test_abort_cleans_temp(storage):
    staged = await storage.create_staging("abort.txt")
    await storage.append(staged, b"data")
    assert os.path.exists(staged.temp_path)
    await storage.abort(staged)
    assert not os.path.exists(staged.temp_path)


@pytest.mark.asyncio
async def test_streaming_large_file(storage):
    staged = await storage.create_staging("large.bin")
    chunk = b"A" * (64 * 1024)
    num_chunks = 100  # ~6.4 MB
    for _ in range(num_chunks):
        await storage.append(staged, chunk)
    stored = await storage.commit(staged)
    assert stored.size == num_chunks * 64 * 1024

    total_read = 0
    async for c in storage.open_read(stored.storage_key):
        total_read += len(c)
    assert total_read == stored.size
