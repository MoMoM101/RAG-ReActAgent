"""Validated request/response models for the settings API."""

import re

from pydantic import BaseModel, field_validator


class _ProviderSettings(BaseModel):
    provider: str
    model: str
    api_key: str = ""
    base_url: str

    @field_validator("provider")
    @classmethod
    def validate_provider(cls, value: str) -> str:
        if not re.fullmatch(r"[a-z0-9_-]+", value):
            raise ValueError("Provider must be alphanumeric")
        return value

    @field_validator("base_url")
    @classmethod
    def validate_base_url(cls, value: str) -> str:
        if value and not re.match(r"^https?://", value):
            raise ValueError("Base URL must start with http:// or https://")
        return value


class LLMSettings(_ProviderSettings):
    provider: str = "openai"
    model: str = "gpt-4o"
    base_url: str = "https://api.openai.com/v1"


class EmbeddingSettings(_ProviderSettings):
    provider: str = "openai"
    model: str = "text-embedding-3-small"
    base_url: str = "https://api.openai.com/v1"


class SettingsResponse(BaseModel):
    llm: LLMSettings
    embedding: EmbeddingSettings
    web_search_enabled: bool = True
    rerank_enabled: bool = False
    retrieval_top_k: int = 5
    web_search_max_results: int = 5
    chunk_size: int = 384
    chunk_overlap: int = 50


class TestConnectionRequest(BaseModel):
    provider: str = ""
    model: str = ""
    api_key: str = ""
    base_url: str = ""
    kind: str = "llm"
