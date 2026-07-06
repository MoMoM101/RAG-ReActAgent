from config import settings

from .base import BaseVectorDB
from .qdrant import QdrantVectorDB


async def create_vectordb() -> BaseVectorDB:
    db = QdrantVectorDB()
    from embedding.factory import create_embedding
    embedding = create_embedding()
    test_vec = await embedding.embed_query("dimension test")
    current_dim = len(test_vec)

    if not await db.collection_exists():
        await db.create_collection(current_dim)
        settings.embedding_dim = current_dim
    else:
        existing_dim = await db.get_collection_dim()
        if existing_dim is not None:
            settings.embedding_dim = existing_dim
            if existing_dim != current_dim:
                raise ValueError(
                    f"Embedding dimension mismatch: the vector database has dimension {existing_dim}, "
                    f"but the current embedding model '{settings.embedding_model}' produces dimension {current_dim}. "
                    f"Switching embedding models requires rebuilding the index. "
                    f"Please delete the existing collection (or the Qdrant data directory) and re-ingest all documents."
                )
    return db
