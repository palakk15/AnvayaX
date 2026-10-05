"""Small, non-executing local configuration reader. Never returns keys to the UI."""
from __future__ import annotations

import json
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
    for key in ("GROQ_API_KEYS", "GROQ_API_KEY", "GROQ_MODEL", "BHARATBLEND_DATA_DIR"):
        if key in os.environ:
            values[key] = os.environ[key]
    return values


def groq_keys(values: dict[str, str]) -> list[str]:
    raw = values.get("GROQ_API_KEYS", "").strip()
    if raw.startswith("["):
        try:
            keys = json.loads(raw)
        except ValueError:
            raise ValueError("GROQ_API_KEYS must be a JSON list of strings or comma-separated keys.") from None
        if not isinstance(keys, list) or any(not isinstance(k, str) for k in keys):
            raise ValueError("GROQ_API_KEYS must contain only strings.")
    else:
        keys = raw.split(",") if raw else []
    if values.get("GROQ_API_KEY"):
        keys.append(values["GROQ_API_KEY"])
    return list(dict.fromkeys(k.strip() for k in keys if k.strip()))
