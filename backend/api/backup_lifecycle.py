"""Restore artifact and Qdrant collection lifecycle management."""

import asyncio
import logging
import shutil
from pathlib import Path

from config import settings

logger = logging.getLogger(__name__)
RESTORE_TEMP_PREFIX = "rag_chunks_restore_"


def cleanup_restore_artifacts(upload_dir: Path) -> list[str]:
    """Remove candidate/previous upload directories left by interrupted restores."""
    cleaned: list[str] = []
    parent = upload_dir.resolve()
    for pattern in ("uploads.candidate.*", "uploads.previous.*"):
        for path in sorted(parent.parent.glob(pattern)):
            try:
                shutil.rmtree(path, ignore_errors=False)
                cleaned.append(path.name)
                logger.warning("startup cleanup: removed leftover %s", path.name)
            except OSError as exc:
                logger.warning(
                    "startup cleanup: could not remove %s: %s",
                    path.name,
                    exc,
                )
    return cleaned


async def delete_restore_temp_collection(collection_name: str) -> bool:
    try:
        from vectordb.qdrant import QdrantVectorDB

        database = QdrantVectorDB(collection_name=collection_name)
        database.client.delete_collection(collection_name)
        logger.info("deleted restore temp collection: %s", collection_name)
        return True
    except Exception as exc:
        logger.warning(
            "failed to delete restore temp collection %s: %s",
            collection_name,
            exc,
        )
        return False


async def cleanup_orphan_qdrant_collections(active: str) -> dict:
    """Audit collections conservatively without deleting unverifiable data."""
    from vectordb.qdrant import QdrantVectorDB

    metrics = {"audited": 0, "deleted": 0, "kept": 0, "errors": 0}
    try:
        database = QdrantVectorDB()
        collections = await asyncio.to_thread(database.client.get_collections)
    except Exception as exc:
        logger.warning("startup collection audit failed: %s", exc)
        return metrics

    active_base = settings.qdrant_collection
    for collection in collections.collections:
        metrics["audited"] += 1
        # Restore temp collections are deliberately retained here because
        # Qdrant local mode exposes no reliable creation timestamp.
        if collection.name in (active, active_base):
            metrics["kept"] += 1
        else:
            metrics["kept"] += 1

    logger.info(
        "startup collection audit: %d total, %d deleted, %d kept, %d errors",
        metrics["audited"],
        metrics["deleted"],
        metrics["kept"],
        metrics["errors"],
    )
    return metrics


async def retain_collections_after_restore(
    new_active: str,
    old_active: str,
    temp_collection: str,
) -> dict:
    """Keep active/rollback collections and remove other restore temporaries."""
    from vectordb.qdrant import QdrantVectorDB

    metrics = {"deleted": 0, "errors": 0, "kept": 0}
    try:
        database = QdrantVectorDB()
        collections = await asyncio.to_thread(database.client.get_collections)
    except Exception as exc:
        logger.warning("post-restore collection cleanup failed: %s", exc)
        return metrics

    keep = {new_active, old_active, settings.qdrant_collection}
    for collection in collections.collections:
        name = collection.name
        if name in keep:
            metrics["kept"] += 1
        elif name.startswith(RESTORE_TEMP_PREFIX):
            if await delete_restore_temp_collection(name):
                metrics["deleted"] += 1
            else:
                metrics["errors"] += 1
        else:
            metrics["kept"] += 1

    logger.info(
        "post-restore collection lifecycle: deleted=%d, kept=%d, errors=%d",
        metrics["deleted"],
        metrics["kept"],
        metrics["errors"],
    )
    return metrics
