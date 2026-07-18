"""Grep the backend source for SQLite-specific SQL patterns."""
import re
from pathlib import Path

PATTERNS = [
    (r"PRAGMA\s+\w+", "PRAGMA"),
    (r"CREATE\s+VIRTUAL\s+TABLE", "FTS5 VIRTUAL TABLE"),
    (r"INSERT\s+OR\s+REPLACE", "INSERT OR REPLACE"),
    (r"datetime\('now'\)", "datetime('now')"),
    (r"strftime\(", "strftime"),
    (r"fts5", "FTS5 reference", re.IGNORECASE),
]

def scan(path: Path):
    results = []
    for f in sorted(path.rglob("*.py")):
        if "site-packages" in str(f) or "__pycache__" in str(f):
            continue
        try:
            text = f.read_text(encoding="utf-8")
        except Exception:
            continue
        for line_no, line in enumerate(text.splitlines(), 1):
            for pattern, label, *flags in PATTERNS:
                flag = flags[0] if flags else 0
                if re.search(pattern, line, flag):
                    stripped = line.strip()
                    if stripped.startswith("#") or stripped.startswith('"""') or stripped.startswith("'''"):
                        continue
                    results.append(f"{f}:{line_no}: [{label}] {stripped[:120]}")
    return results


if __name__ == "__main__":
    backend = Path(__file__).resolve().parent.parent / "backend"
    for r in scan(backend):
        print(r)
