"""Minimal .env loader.

A terminal `export` only lives in that terminal's own process; it never
reaches a process this tool starts separately. A file survives across
processes, so the key goes in `.env` (git-ignored) instead. This loader is
deliberately a dozen lines rather than a new pinned dependency: the project's
own discipline elsewhere (`web/server.py`) is stdlib-only, and a KEY=VALUE
file needs nothing more.

Real environment variables always win over the file, so `export
GROQ_API_KEY=...` in whatever shell actually launches a script still takes
priority if both are present.
"""

from __future__ import annotations

import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def load_env(path: Path | str | None = None) -> None:
    env_path = Path(path) if path else ROOT / ".env"
    if not env_path.exists():
        return
    for line in env_path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key, value = key.strip(), value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


def require(name: str, hint: str = "") -> str:
    load_env()
    value = os.environ.get(name)
    if not value:
        extra = f" {hint}" if hint else ""
        raise RuntimeError(
            f"{name} is not set.{extra} Copy .env.example to .env and fill "
            f"it in, or export {name} in the shell that runs this script.")
    return value
