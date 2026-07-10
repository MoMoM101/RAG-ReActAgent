"""Test that .env.example covers all Settings fields in config.py."""
from pathlib import Path


def _parse_settings_fields() -> set[str]:
    """Extract all field names from config.Settings class (UPPERCASED)."""
    from config import Settings

    fields = set()
    for name in Settings.model_fields:
        if name == "model_config":
            continue
        fields.add(name.upper())
    return fields


def _parse_env_example_keys() -> set[str]:
    """Extract all key names from .env.example."""
    env_example = Path(__file__).resolve().parent.parent / ".env.example"
    if not env_example.exists():
        return set()

    keys = set()
    for line in env_example.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if "=" in stripped:
            key = stripped.split("=", 1)[0].strip()
            keys.add(key.upper())
    return keys


def test_env_example_covers_all_settings():
    """.env.example should have an entry for every config.Settings field."""
    settings_fields = _parse_settings_fields()
    env_keys = _parse_env_example_keys()

    missing = settings_fields - env_keys
    extra = env_keys - settings_fields

    assert not missing, (
        f".env.example 缺少配置项: {sorted(missing)}\n"
        f"请检查 config.py 中 Settings 类与 .env.example 的对应关系"
    )
    assert not extra, (
        f".env.example 包含不在 config.py 中的额外键: {sorted(extra)}\n"
        f"请更新 config.py 或从 .env.example 移除多余字段"
    )
