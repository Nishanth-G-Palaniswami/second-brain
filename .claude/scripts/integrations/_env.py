"""Tiny stdlib-only .env loader.

Avoids the python-dotenv dependency; we only need `KEY=value` parsing for a
handful of integration tokens. Precedence: process env wins over .env file.
"""
from __future__ import annotations

import os
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[3]
ENV_FILE = PROJECT_ROOT / ".env"

_cache: dict[str, str] | None = None


def _load_file() -> dict[str, str]:
    global _cache
    if _cache is not None:
        return _cache
    out: dict[str, str] = {}
    if ENV_FILE.exists():
        for raw in ENV_FILE.read_text(encoding="utf-8").splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, val = line.partition("=")
            key = key.strip()
            val = val.strip()
            if len(val) >= 2 and val[0] == val[-1] and val[0] in ("'", '"'):
                val = val[1:-1]
            out[key] = val
    _cache = out
    return out


def get(key: str, default: str | None = None) -> str | None:
    """Read an env var — process env first, then `.env` file."""
    return os.environ.get(key) or _load_file().get(key, default)


def require(key: str, hint: str = "") -> str:
    val = get(key)
    if not val:
        extra = f" — {hint}" if hint else ""
        raise RuntimeError(f"Missing env var {key}. Add it to .env{extra}")
    return val
