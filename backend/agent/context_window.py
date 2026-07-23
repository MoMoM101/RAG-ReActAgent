"""Adaptive context window — JSON lookup for initial value, binary-search on error."""

import json
import logging
from pathlib import Path

logger = logging.getLogger(__name__)

_MIN_WINDOW = 16000
_DEFAULT_WINDOW = 128000

_contexts: dict[str, int] | None = None


def _load_contexts() -> dict[str, int]:
    global _contexts
    if _contexts is None:
        path = Path(__file__).resolve().parent.parent / "model_contexts.json"
        with open(path, encoding="utf-8") as f:
            _contexts = json.load(f)
    return _contexts


def _lookup_model(model_id: str) -> int | None:
    contexts = _load_contexts()
    if model_id in contexts:
        return contexts[model_id]
    for prefix in sorted(contexts, key=lambda x: -len(x)):
        if model_id.startswith(prefix):
            return contexts[prefix]
    return None


def get_window() -> int:
    """Return current context window. Checks .env override -> JSON -> default."""
    from config import settings

    if settings.llm_max_context > 0:
        return settings.llm_max_context

    looked_up = _lookup_model(settings.llm_model)
    if looked_up is not None:
        return looked_up

    logger.info(
        "model %s not in model_contexts.json, defaulting to %d",
        settings.llm_model, _DEFAULT_WINDOW,
    )
    return _DEFAULT_WINDOW


def is_context_error(exc: Exception) -> bool:
    """Check if an exception is a context-length-related error."""
    msg = str(exc).lower()
    return any(kw in msg for kw in (
        "context_length_exceeded",
        "maximum context length",
        "reduce the length",
        "too long",
        "requested token count exceeds",
    ))


class ContextDetectionError(RuntimeError):
    """Raised when context window detection fails at minimum budget."""


def reset_for_testing() -> None:
    """Reset global caches — for tests only."""
    global _contexts
    _contexts = None
