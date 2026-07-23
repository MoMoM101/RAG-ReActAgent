"""Read-only consistency check intended to run inside the backend container."""

from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path

_script_path = Path(__file__).resolve()
_default_app_root = _script_path.parents[2] if len(_script_path.parents) > 2 else Path("/app")
sys.path.insert(0, str(Path(os.environ.get("RAG_AGENT_APP_ROOT", _default_app_root))))

from sqlalchemy import select

from config import settings
from models.database import session_scope
from models.orm import DocStatus, Document, GenerationStatus, IndexGeneration
from textdb.bm25_search import BM25Search
from vectordb.qdrant import QdrantVectorDB


def _restore_active_collection_pointer() -> None:
    pointer = Path(settings.qdrant_path) / "active_collections.json"
    if not pointer.is_file():
        return
    data = json.loads(pointer.read_text(encoding="utf-8"))
    settings.qdrant_active_collection = data.get("rag_chunks", "")


async def check_live_indexes() -> dict[str, int]:
    _restore_active_collection_pointer()
    async with session_scope() as session:
        result = await session.execute(select(Document).where(Document.status == DocStatus.ready))
        documents = result.scalars().all()
        if not documents:
            raise RuntimeError("No ready documents found in live database")

        bm25 = BM25Search()
        vectors = QdrantVectorDB()
        checked_chunks = 0
        for document in documents:
            if not document.active_generation_id:
                raise RuntimeError(f"Document {document.id} has no active generation")
            generation_result = await session.execute(
                select(IndexGeneration).where(IndexGeneration.id == document.active_generation_id)
            )
            generation = generation_result.scalar_one_or_none()
            if generation is None or generation.status != GenerationStatus.committed:
                raise RuntimeError(
                    f"Document {document.id} generation is missing or not committed"
                )

            bm25_ids = set(await bm25.get_chunk_ids_by_document(document.id))
            vector_ids = set(await vectors.get_chunk_ids_by_document(document.id))
            if bm25_ids != vector_ids:
                raise RuntimeError(
                    f"Document {document.id} index mismatch: "
                    f"bm25_only={sorted(bm25_ids - vector_ids)[:5]} "
                    f"vector_only={sorted(vector_ids - bm25_ids)[:5]}"
                )
            if len(bm25_ids) != document.chunk_count:
                raise RuntimeError(
                    f"Document {document.id} chunk count mismatch: "
                    f"index={len(bm25_ids)} document={document.chunk_count}"
                )
            checked_chunks += len(bm25_ids)

    return {"documents": len(documents), "chunks": checked_chunks}


if __name__ == "__main__":
    print(json.dumps(asyncio.run(check_live_indexes()), ensure_ascii=False))
