from rag.pipeline import ingest_document


async def ingest_document_async(filename: str, file_content: bytes, file_type: str) -> str:
    """Wrapper for background task execution."""
    return await ingest_document(filename, file_content, file_type)
