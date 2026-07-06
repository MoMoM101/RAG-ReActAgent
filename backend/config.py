from pathlib import Path

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    # LLM
    llm_provider: str = "openai"
    llm_model: str = "gpt-4o"
    llm_api_key: str = ""
    llm_base_url: str = "https://api.openai.com/v1"

    # Embedding
    embedding_provider: str = "openai"
    embedding_model: str = "text-embedding-3-small"
    embedding_api_key: str = ""
    embedding_base_url: str = "https://api.openai.com/v1"
    embedding_dim: int = 1536

    # Qdrant
    qdrant_host: str = ""
    qdrant_port: int = 6333
    qdrant_path: str = "./data/qdrant2"
    qdrant_collection: str = "rag_chunks"
    qdrant_active_collection: str = ""  # 运行时覆盖，空则用 qdrant_collection

    # SQLite
    database_url: str = "sqlite+aiosqlite:///./data/rag_agent.db"

    # Storage
    upload_dir: str = "./data/uploads"

    # LLM context window (0 = auto-detect from JSON mapping or default)
    llm_max_context: int = 0

    # Agent
    max_loop_iterations: int = 10
    max_tool_retries: int = 3
    max_total_time: int = 120
    chunk_size: int = 512
    chunk_overlap: int = 50
    retrieval_top_k: int = 8

    # Retrieval dedup
    dedup_enabled: bool = True
    dedup_similarity_threshold: float = 0.85

    # Memory
    memory_enabled: bool = True
    memory_max_count: int = 100

    # Reranker
    rerank_enabled: bool = True
    rerank_model: str = "BAAI/bge-reranker-v2-m3"
    rerank_top_n: int = 16
    hf_endpoint: str = ""  # HuggingFace 镜像，国内设 https://hf-mirror.com
    chunk_quality_filter_enabled: bool = True  # 设为 False 对代码/API 文档等场景禁用质量过滤

    # Web Search
    web_search_enabled: bool = True
    web_search_max_results: int = 5
    web_search_proxy: str = ""

    # OCR
    ocr_enabled: bool = True
    ocr_min_text_length: int = 50

    # Ingestion
    ingestion_max_concurrency: int = 3

    # Logging
    log_level: str = "INFO"

    # Server
    secret_key: str = "change-me-in-production"

    model_config = {
        "env_file": str(Path(__file__).resolve().parent / ".env"),
        "env_file_encoding": "utf-8",
    }


import secrets


def _write_env_key(env_path: Path, key: str, value: str) -> None:
    """在 .env 文件中写入或更新单个键值。"""
    if env_path.exists():
        lines = env_path.read_text(encoding="utf-8").splitlines()
        new_lines = []
        found = False
        for line in lines:
            stripped = line.strip()
            if stripped.startswith(f"{key}=") or stripped.startswith(f"{key} "):
                new_lines.append(f"{key}={value}")
                found = True
            else:
                new_lines.append(line)
        if not found:
            new_lines.append(f"{key}={value}")
        env_path.write_text("\n".join(new_lines) + "\n", encoding="utf-8")
    else:
        env_path.write_text(f"{key}={value}\n", encoding="utf-8")


def _restore_collection_pointers(s: Settings) -> None:
    """从 active_collections.json 恢复运行时 collection 名称指针。"""
    import json as _json
    ptr_file = Path(s.qdrant_path) / "active_collections.json"
    if not ptr_file.exists():
        return
    try:
        pointers = _json.loads(ptr_file.read_text())
    except Exception:
        return
    if pointers.get("rag_chunks"):
        s.qdrant_active_collection = pointers["rag_chunks"]
    if pointers.get("user_profile"):
        try:
            import memory.profile as _pf
            _pf.PROFILE_COLLECTION = pointers["user_profile"]
        except Exception:
            pass


def _init_settings() -> Settings:
    """加载配置，自动处理 secret_key 生成和 API Key 解密。"""
    s = Settings()

    env_file = s.model_config.get("env_file", ".env")
    env_path = Path(str(env_file)) if not isinstance(env_file, Path) else env_file

    # secret_key 为默认值时自动生成随机密钥（写入前检查避免多 worker 竞争）
    if s.secret_key == "change-me-in-production":
        import re as _re
        if env_path.exists():
            text = env_path.read_text(encoding="utf-8")
            m = _re.search(r"^SECRET_KEY=(.+)", text, _re.MULTILINE)
            if m:
                s.secret_key = m.group(1).strip()
            else:
                s.secret_key = secrets.token_urlsafe(32)
                _write_env_key(env_path, "SECRET_KEY", s.secret_key)
        else:
            s.secret_key = secrets.token_urlsafe(32)
            _write_env_key(env_path, "SECRET_KEY", s.secret_key)

    # 解密 API Key
    from utils.crypto import decrypt_if_needed
    if s.llm_api_key:
        s.llm_api_key = decrypt_if_needed(s.llm_api_key, s.secret_key)
    if s.embedding_api_key:
        s.embedding_api_key = decrypt_if_needed(s.embedding_api_key, s.secret_key)

    # 从 active_collections.json 恢复运行时 collection 指针（重建/清空操作后持久化的）
    _restore_collection_pointers(s)

    return s


settings = _init_settings()
