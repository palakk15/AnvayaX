"""Small, non-executing local configuration reader. Never returns keys to the UI."""
from __future__ import annotations

import os
from pathlib import Path


def local_settings(path: Path | str = ".env") -> dict[str, str]:
    values = {}
    file = Path(path)
    if file.is_file():
        for line in file.read_text(encoding="utf-8-sig").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            key, value = key.strip(), value.strip()
            if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
                value = value[1:-1]
            values[key] = value
    for key in ("GROQ_API_KEY", "BHARATBLEND_DATA_DIR"):
        if key in os.environ:
            values[key] = os.environ[key]
    return values


def groq_key(values: dict[str, str]) -> str | None:
    """Return the sole server credential; ignore legacy multi-key settings."""
    return values.get("GROQ_API_KEY", "").strip() or None
