"""Persist manually resolved reading status by canonical book key."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from pathlib import Path

from .formatting import format_datetime


_APPLICATION_DIRECTORY = "kindle-export-explorer"
_RESOLUTION_FILENAME = "resolution.json"


class ResolutionError(ValueError):
    """Raised when stored manual resolutions cannot be read or written."""


class ReadStatus(StrEnum):
    YES = "yes"
    NO = "no"
    PARTIALLY = "partially"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class ManualResolution:
    key: str
    resolution: ReadStatus
    updated_at: datetime


def resolution_path() -> Path:
    """Return the resolution file under XDG_CONFIG_HOME or ~/.config."""
    configured = os.environ.get("XDG_CONFIG_HOME")
    config_home = Path(configured).expanduser() if configured else Path.home() / ".config"
    return config_home / _APPLICATION_DIRECTORY / _RESOLUTION_FILENAME


def load_resolutions(path: Path | None = None) -> dict[str, ManualResolution]:
    """Load resolutions by canonical key; a missing file represents no decisions."""
    resolved_path = path or resolution_path()
    if not resolved_path.exists():
        return {}
    try:
        value = json.loads(resolved_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ResolutionError(f"cannot read {resolved_path}: {exc}") from exc
    if not isinstance(value, list):
        raise ResolutionError(f"cannot read {resolved_path}: expected a JSON array")

    result: dict[str, ManualResolution] = {}
    for index, item in enumerate(value):
        if not isinstance(item, dict):
            raise ResolutionError(
                f"cannot read {resolved_path}: record {index} must be an object"
            )
        key = item.get("key")
        resolution_value = item.get("resolution")
        updated_at_value = item.get("updated_at")
        if not isinstance(key, str) or not key:
            raise ResolutionError(
                f"cannot read {resolved_path}: record {index} has an invalid key"
            )
        if key in result:
            raise ResolutionError(f"cannot read {resolved_path}: duplicate key {key}")
        if not isinstance(resolution_value, str):
            raise ResolutionError(
                f"cannot read {resolved_path}: record {index} has an invalid resolution"
            )
        try:
            resolution = ReadStatus(resolution_value)
        except ValueError as exc:
            raise ResolutionError(
                f"cannot read {resolved_path}: record {index} has an invalid resolution"
            ) from exc
        if not isinstance(updated_at_value, str):
            raise ResolutionError(
                f"cannot read {resolved_path}: record {index} has an invalid updated_at"
            )
        try:
            updated_at = datetime.fromisoformat(
                updated_at_value.replace("Z", "+00:00")
            )
        except ValueError as exc:
            raise ResolutionError(
                f"cannot read {resolved_path}: record {index} has an invalid updated_at"
            ) from exc
        result[key] = ManualResolution(key, resolution, updated_at)
    return result


def save_resolutions(
    resolutions: dict[str, ManualResolution],
    path: Path | None = None,
) -> None:
    """Atomically replace the resolution file with decisions ordered by key."""
    resolved_path = path or resolution_path()
    temporary_path = resolved_path.with_name(f".{resolved_path.name}.tmp")
    records = [
        {
            "key": item.key,
            "resolution": item.resolution.value,
            "updated_at": format_datetime(item.updated_at),
        }
        for item in sorted(resolutions.values(), key=lambda item: item.key)
    ]
    try:
        resolved_path.parent.mkdir(parents=True, exist_ok=True)
        temporary_path.write_text(
            json.dumps(records, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        temporary_path.replace(resolved_path)
    except (OSError, UnicodeError) as exc:
        raise ResolutionError(f"cannot write {resolved_path}: {exc}") from exc
