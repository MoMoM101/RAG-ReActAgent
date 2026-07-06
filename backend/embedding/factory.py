from config import settings

from .base import BaseEmbedding
from .openai_embed import OpenAIEmbedding

OPENAI_COMPATIBLE = {"openai", "deepseek", "zhipu", "moonshot", "qwen", "custom"}

_embedding_instance: BaseEmbedding | None = None


def create_embedding() -> BaseEmbedding:
    global _embedding_instance
    if _embedding_instance is not None:
        return _embedding_instance
    if settings.embedding_provider in OPENAI_COMPATIBLE:
        _embedding_instance = OpenAIEmbedding()
        return _embedding_instance
    raise ValueError(f"Unsupported embedding provider: {settings.embedding_provider}")


def reset_embedding() -> None:
    """仅测试用：重置 embedding 单例。"""
    global _embedding_instance
    _embedding_instance = None
