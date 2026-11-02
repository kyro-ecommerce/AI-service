"""Resolve a local shared token without copying it into tracked configuration."""

from pathlib import Path
from typing import Mapping

from dotenv import dotenv_values


def resolve_internal_token(environ: Mapping[str, str], project_root: Path) -> str:
    # An explicit value, including an empty value, always wins.
    if "INTERNAL_API_TOKEN" in environ:
        return environ["INTERNAL_API_TOKEN"]
    backend_env = environ.get("KYRO_BACKEND_ENV_FILE")
    if not backend_env:
        return ""
    path = Path(backend_env)
    if not path.is_absolute():
        path = project_root / path
    try:
        if not path.is_file():
            return ""
        return dotenv_values(path).get("INTERNAL_API_TOKEN") or ""
    except OSError:
        return ""
