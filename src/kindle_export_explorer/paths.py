"""Resolve the export path used by command-line entry points."""

from __future__ import annotations

import os
from pathlib import Path


KINDLE_EXPORT_PATH = "KINDLE_EXPORT_PATH"


def resolve_export_path(argument: Path | None) -> Path:
    """Prefer an explicit path, falling back to ``KINDLE_EXPORT_PATH``."""
    if argument is not None:
        return argument.expanduser().resolve()
    environment_path = os.environ.get(KINDLE_EXPORT_PATH)
    if environment_path:
        return Path(environment_path).expanduser().resolve()
    raise ValueError(f"provide EXPORT_PATH or set {KINDLE_EXPORT_PATH}")
