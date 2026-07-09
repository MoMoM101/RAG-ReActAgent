import os
from contextlib import suppress
from pathlib import Path

from config import settings

UPLOAD_DIR = Path(settings.upload_dir).resolve()


def _safe_path(filename: str) -> Path:
    """Resolve a file path inside UPLOAD_DIR, blocking path traversal."""
    # Strip any directory components from the filename
    safe_name = Path(filename).name
    file_path = (UPLOAD_DIR / safe_name).resolve()
    if not str(file_path).startswith(str(UPLOAD_DIR)):
        raise ValueError("Invalid filename: path traversal detected")
    return file_path


def save_upload(file_content: bytes, filename: str) -> str:
    """Save uploaded file, return stored path."""
    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    file_path = _safe_path(filename)

    # Handle duplicate filenames
    if file_path.exists():
        stem, suffix = file_path.stem, file_path.suffix
        counter = 1
        while file_path.exists():
            file_path = _safe_path(f"{stem}_{counter}{suffix}")
            counter += 1

    file_path.write_bytes(file_content)
    return str(file_path)


def delete_file(file_path: str) -> None:
    """Delete uploaded file from disk. Only allows deletion within UPLOAD_DIR."""
    resolved = Path(file_path).resolve()
    if not str(resolved).startswith(str(UPLOAD_DIR)):
        return
    with suppress(FileNotFoundError):
        os.remove(file_path)
