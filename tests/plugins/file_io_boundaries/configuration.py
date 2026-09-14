"""Load file I/O boundary policy from ``pyproject.toml``."""

from __future__ import annotations

import tomllib
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import cast

from .model import Boundary


_CONFIG_LOCATION = "tool.kindle-export-explorer.file-io-boundaries"


@dataclass(frozen=True)
class FileIOConfiguration:
    source_roots: tuple[Path, ...]
    allowed_reads: frozenset[Boundary]
    allowed_writes: frozenset[Boundary] = frozenset()

    @classmethod
    def load(cls, path: Path) -> FileIOConfiguration:
        section = _nested_table(
            _load_toml(path),
            _CONFIG_LOCATION.split("."),
            str(path),
        )
        location = f"{path}: {_CONFIG_LOCATION}"
        roots = _string_list(section.get("source-roots"), f"{location}.source-roots")
        if not roots:
            raise ValueError(f"{location}.source-roots must not be empty")

        reads = _string_list(
            section.get("allowed-reads"),
            f"{location}.allowed-reads",
        )
        writes = _optional_string_list(
            section.get("allowed-writes"),
            f"{location}.allowed-writes",
        )
        base = path.parent.resolve()
        return cls(
            source_roots=tuple((base / root).resolve() for root in roots),
            allowed_reads=frozenset(map(Boundary.parse, reads)),
            allowed_writes=frozenset(map(Boundary.parse, writes)),
        )


def _load_toml(path: Path) -> dict[str, object]:
    try:
        with path.open("rb") as stream:
            value: object = tomllib.load(stream)
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise ValueError(f"cannot read file I/O configuration {path}: {exc}") from exc
    return _string_keyed_table(value, str(path))


def _nested_table(
    table: dict[str, object],
    keys: Sequence[str],
    location: str,
) -> dict[str, object]:
    for key in keys:
        location = f"{location}: {key}"
        table = _string_keyed_table(table.get(key), location)
    return table


def _string_keyed_table(value: object, location: str) -> dict[str, object]:
    if not isinstance(value, dict):
        raise ValueError(f"{location}: expected a table")
    table = cast(dict[object, object], value)
    if not all(isinstance(key, str) for key in table):
        raise ValueError(f"{location}: expected string keys")
    return {str(key): item for key, item in table.items()}


def _optional_string_list(value: object, location: str) -> list[str]:
    return [] if value is None else _string_list(value, location)


def _string_list(value: object, location: str) -> list[str]:
    if not isinstance(value, list):
        raise ValueError(f"{location}: expected an array of strings")
    items = cast(list[object], value)
    if not all(isinstance(item, str) for item in items):
        raise ValueError(f"{location}: expected an array of strings")
    return [item for item in items if isinstance(item, str)]
