from config import settings

from .base import BaseLLM
from .openai_llm import OpenAILLM

OPENAI_COMPATIBLE = {"openai", "deepseek", "zhipu", "moonshot", "qwen", "groq", "ollama", "custom"}

_llm_instance: BaseLLM | None = None


def create_llm() -> BaseLLM:
    global _llm_instance
    if _llm_instance is not None:
        return _llm_instance
    if settings.llm_provider in OPENAI_COMPATIBLE:
        _llm_instance = OpenAILLM()
        return _llm_instance
    raise ValueError(f"Unsupported LLM provider: {settings.llm_provider}")


def reset_llm() -> None:
    """仅测试用：重置 LLM 单例。"""
    global _llm_instance
    _llm_instance = None

