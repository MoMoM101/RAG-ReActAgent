from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings

DOCUMENT_UPLOAD_HARD_LIMIT_MB = 512


class Settings(BaseSettings):
    # LLM
    llm_provider: str = "openai"
    llm_model: str = "gpt-4o"
    llm_api_key: str = ""
    llm_base_url: str = "https://api.openai.com/v1"
    # DeepSeek V4 defaults to thinking mode, which can add several seconds
    # before grounded answers. Keep it off for latency-sensitive RAG; the
    # option is ignored for providers/models without an explicit V4 toggle.
    llm_thinking_enabled: bool = False

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
    document_max_upload_mb: int = Field(
        default=200,
        ge=1,
        le=DOCUMENT_UPLOAD_HARD_LIMIT_MB,
    )
    document_batch_max_files: int = Field(default=50, ge=2, le=200)
    document_batch_max_total_mb: int = Field(default=1024, ge=1, le=10240)

    # LLM context window (0 = auto-detect from JSON mapping or default)
    llm_max_context: int = 0

    # Agent
    max_loop_iterations: int = 10
    max_tool_retries: int = 3
    max_total_time: int = 120
    chunk_size: int = 200
    chunk_overlap: int = 40
    retrieval_top_k: int = 8

    # Timeouts (seconds)
    llm_connect_timeout: float = 10.0
    llm_read_timeout: float = 60.0
    llm_first_token_timeout: float = 30.0
    tool_default_timeout: float = 60.0
    embedding_timeout: float = 30.0

    # LLM retry
    llm_max_retries: int = 3
    llm_retry_backoff: float = 1.0

    # Retrieval dedup
    dedup_enabled: bool = True
    dedup_similarity_threshold: float = 0.85

    # Memory
    memory_enabled: bool = True
    memory_max_count: int = 100

    # Reranker
    rerank_enabled: bool = False
    rerank_model: str = "BAAI/bge-reranker-v2-m3"
    rerank_top_n: int = 8
    rrf_k: int = 60
    rrf_semantic_weight: float = 2.0
    rrf_keyword_weight: float = 1.0
    rrf_quality_prefilter_enabled: bool = False
    rrf_adaptive_enabled: bool = False
    hf_endpoint: str = ""  # HuggingFace 镜像，国内设 https://hf-mirror.com
    chunk_quality_filter_enabled: bool = True  # 设为 False 对代码/API 文档等场景禁用质量过滤
    chunk_quality_llm_enabled: bool = False  # 启用 LLM 对低质量 chunk 批量判分（会增加搜索延迟）
    query_rewrite_enabled: bool = False  # 启用多路查询改写，搜索前用 LLM 生成关键词变体并行检索

    # Grounded answer verification
    grounding_verification_enabled: bool = True
    grounding_min_coverage: float = 0.70
    grounding_enforcement: str = "report"  # off | report | strict

    # ── V4 Grounding repair ──
    # Stream verification: verify atomic units during generation and send immediately
    grounding_stream_verify_enabled: bool = False

    # Deterministic citation repair (no LLM)
    grounding_deterministic_repair_enabled: bool = True
    grounding_auto_cite_min_score: float = 0.55
    grounding_auto_cite_min_margin: float = 0.15

    # Bounded LLM repair
    grounding_repair_enabled: bool = True
    grounding_repair_model: str = ""  # empty = reuse primary LLM
    grounding_repair_timeout: float = 4.0
    grounding_repair_max_tokens: int = 350
    grounding_repair_max_attempts: int = 1

    # Answer cache
    rag_answer_cache_enabled: bool = True
    rag_answer_cache_ttl_seconds: int = 300
    rag_answer_cache_max_entries: int = 1000

    # Output budget (Phase 5): max tokens for main generation, 0 = unlimited
    rag_generation_max_tokens: int = 0
    # One bounded recovery call when reasoning consumes the final-answer budget.
    rag_truncation_recovery_enabled: bool = True
    rag_truncation_recovery_max_tokens: int = 2048

    # Performance gates (metrics only; enforcement requires separate monitoring)
    rag_ttft_p95_target_ms: int = 2500
    rag_total_p95_target_ms: int = 5000

    # Phase-level timeout budgets (seconds, 0 = unlimited)
    rag_timeout_intent: float = 5.0       # intent classification
    rag_timeout_retrieval: float = 10.0   # retrieval (semantic + BM25 + rerank)
    rag_timeout_rerank: float = 5.0       # rerank budget within retrieval; on timeout fall back to RRF order
    rag_timeout_generation: float = 60.0  # LLM draft generation
    rag_timeout_verification: float = 5.0 # verification
    rag_timeout_repair: float = 10.0      # LLM repair (reuses grounding_repair_timeout)

    # Web Search
    web_search_enabled: bool = False
    web_search_max_results: int = 5
    web_search_proxy: str = ""

    # OCR
    ocr_enabled: bool = True
    ocr_min_text_length: int = 50

    # Ingestion
    ingestion_max_concurrency: int = 3

    # Ingestion retry
    ingestion_max_retries: int = 3        # max attempts including first try
    ingestion_retry_base_sec: float = 5.0  # base backoff seconds
    ingestion_retry_max_sec: float = 300.0 # backoff cap
    ingestion_retry_jitter: float = 0.3    # jitter ratio (0-1)

    # Logging
    log_level: str = "INFO"

    # Backup / Restore
    backup_max_upload_mb: int = 512
    backup_max_extracted_mb: int = 2048
    backup_max_members: int = 10000

    # Server
    secret_key: str = "change-me-in-production"
    server_host: str = "127.0.0.1"
    allow_remote_access: bool = False
    admin_api_token: str = ""

    # JWT
    jwt_secret: str = ""                           # auto-generated if empty
    jwt_access_token_expire_minutes: int = 60      # 1 hour
    jwt_refresh_token_expire_days: int = 7         # 7 days
    legacy_admin_token_enabled: bool = True        # transition period

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

    # Resolve CWD-dependent paths to absolute (based on backend directory)
    _backend_dir = Path(__file__).resolve().parent
    # database_url: sqlite+aiosqlite:///./data/... → absolute path
    if s.database_url.startswith("sqlite+aiosqlite:///") and "./" in s.database_url:
        import re as _re
        db_rel = s.database_url[len("sqlite+aiosqlite:///"):]
        if not Path(db_rel).is_absolute():
            db_abs = (_backend_dir / db_rel).resolve()
            s.database_url = f"sqlite+aiosqlite:///{db_abs}"
    # upload_dir
    if not Path(s.upload_dir).is_absolute():
        s.upload_dir = str((_backend_dir / s.upload_dir).resolve())
    # qdrant_path
    if not Path(s.qdrant_path).is_absolute():
        s.qdrant_path = str((_backend_dir / s.qdrant_path).resolve())

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
