"""Small, side-effect-explicit helpers for settings environment files."""

from pathlib import Path


def read_env(path: Path) -> dict[str, str]:
    env: dict[str, str] = {}
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                key, _, value = line.partition("=")
                env[key.strip()] = value.strip()
    return env


def write_env(path: Path, updates: dict[str, str]) -> None:
    existing = read_env(path) if path.exists() else {}
    existing.update(updates)
    lines = []
    for key, value in existing.items():
        if any(char in value for char in (" ", "#", "=", '"', "'")):
            escaped = value.replace("\\", "\\\\").replace('"', '\\"')
            lines.append(f'{key}="{escaped}"')
        else:
            lines.append(f"{key}={value}")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def env_bool(env: dict[str, str], key: str, default: bool) -> bool:
    value = env.get(key, "").lower()
    if value in ("true", "1", "yes"):
        return True
    if value in ("false", "0", "no"):
        return False
    return default


def mask_key(key: str) -> str:
    if len(key) <= 8:
        return "***"
    return key[:4] + "***" + key[-4:]
