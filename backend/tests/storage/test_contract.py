import hashlib
from pathlib import Path

import pytest
from storage import materialize, stage_path
from storage.base import StagedObject, StoredObject
from storage.files import LocalFileStorage


class MemoryFileStorage:
    """Minimal non-local backend used to enforce the FileStorage contract."""

    def __init__(self) -> None:
        self.staging: dict[str, bytearray] = {}
        self.objects: dict[str, bytes] = {}

    async def create_staging(self, filename: str) -> StagedObject:
        staged = StagedObject(filename=filename)
        self.staging[staged.staging_id] = bytearray()
        return staged

    async def append(self, staged: StagedObject, chunk: bytes) -> None:
        self.staging[staged.staging_id].extend(chunk)
        staged.size = len(self.staging[staged.staging_id])

    async def commit(
        self,
        staged: StagedObject,
        *,
        expected_sha256: str | None = None,
    ) -> StoredObject:
        content = bytes(self.staging.pop(staged.staging_id))
        digest = hashlib.sha256(content).hexdigest()
        if expected_sha256 and digest != expected_sha256:
            raise ValueError("Hash mismatch")
        key = f"{digest[:2]}/{digest[2:4]}/{digest}"
        self.objects[key] = content
        return StoredObject(key, staged.filename, len(content), digest)

    async def abort(self, staged: StagedObject) -> None:
        self.staging.pop(staged.staging_id, None)

    async def open_read(self, storage_key: str):
        yield self.objects[storage_key]

    async def delete(self, storage_key: str) -> None:
        self.objects.pop(storage_key, None)

    async def exists(self, storage_key: str) -> bool:
        return storage_key in self.objects

    async def clear(self) -> None:
        self.staging.clear()
        self.objects.clear()


@pytest.fixture(params=["local", "memory"])
def storage_backend(request, tmp_path):
    if request.param == "local":
        return LocalFileStorage(str(tmp_path / "objects"))
    return MemoryFileStorage()


@pytest.mark.asyncio
async def test_storage_backend_roundtrip_delete_and_clear(storage_backend):
    first = await storage_backend.create_staging("first.txt")
    await storage_backend.append(first, b"first")
    stored_first = await storage_backend.commit(first)

    chunks = [chunk async for chunk in storage_backend.open_read(stored_first.storage_key)]
    assert b"".join(chunks) == b"first"
    assert await storage_backend.exists(stored_first.storage_key)

    await storage_backend.delete(stored_first.storage_key)
    assert not await storage_backend.exists(stored_first.storage_key)

    second = await storage_backend.create_staging("second.txt")
    await storage_backend.append(second, b"second")
    stored_second = await storage_backend.commit(second)
    await storage_backend.clear()
    assert not await storage_backend.exists(stored_second.storage_key)


@pytest.mark.asyncio
async def test_materialize_supports_local_and_remote_backends(storage_backend):
    staged = await storage_backend.create_staging("report.txt")
    await storage_backend.append(staged, b"materialized")
    stored = await storage_backend.commit(staged)

    async with materialize(storage_backend, stored.storage_key, suffix=".txt") as path:
        assert Path(path).read_bytes() == b"materialized"


@pytest.mark.asyncio
async def test_stage_path_is_backend_neutral(storage_backend, tmp_path):
    source = tmp_path / "source.bin"
    source.write_bytes(b"from-path")

    staged = await stage_path(storage_backend, str(source), "source.bin")
    stored = await storage_backend.commit(staged)

    chunks = [chunk async for chunk in storage_backend.open_read(stored.storage_key)]
    assert b"".join(chunks) == b"from-path"
