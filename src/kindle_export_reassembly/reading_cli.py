"""Command-line interface for inspecting reading records for one book."""

from __future__ import annotations

import json
from dataclasses import asdict, fields
from datetime import date
from pathlib import Path
from typing import Any

import click

from .books import CanonicalKey, ExportError, reconstruct_books
from .reading import BookReading, reconstruct_reading


_OMITTED_RECORD_FIELDS = {
    "asin",
    "device_family",
    "device_serial_number",
    "device_software_version",
    "non_asin",
    "personal_document_id",
    "product_name",
    "preferred_marketplace",
    "purchased_marketplace",
    "reading_marketplace",
    "third_party_device",
}


@click.command(context_settings={"help_option_names": ["-h", "--help"]})
@click.argument("canonical_key")
@click.argument(
    "export_directory",
    type=click.Path(path_type=Path, exists=True, file_okay=False, resolve_path=True),
)
def main(canonical_key: str, export_directory: Path) -> None:
    """Print reading records for CANONICAL_KEY from EXPORT_DIRECTORY as JSON."""
    key = _parse_canonical_key(canonical_key)
    try:
        reading = next(
            (item for item in reconstruct_reading(export_directory) if item.key == key),
            None,
        )
        book = next(
            (
                item
                for item in reconstruct_books(
                    export_directory,
                    show_default=True,
                    show_samples=True,
                    source="all",
                )
                if item.key == key
            ),
            None,
        )
    except ExportError as exc:
        raise click.ClickException(str(exc)) from exc

    if reading is None:
        raise click.ClickException(f"no reading records found for {key}")
    click.echo(
        json.dumps(
            _reading_as_dict(reading, book.title if book else None),
            ensure_ascii=False,
            default=_json_default,
        )
    )


def _parse_canonical_key(value: str) -> CanonicalKey:
    kind, separator, identifier = value.strip().partition(":")
    if not separator or not identifier:
        raise click.BadParameter(
            "expected asin:<ASIN> or document:<DocumentId>",
            param_hint="CANONICAL_KEY",
        )
    if kind.casefold() == "asin":
        return CanonicalKey(asin=identifier.upper())
    if kind.casefold() == "document":
        return CanonicalKey(document_id=identifier)
    raise click.BadParameter(
        "expected asin:<ASIN> or document:<DocumentId>",
        param_hint="CANONICAL_KEY",
    )


def _reading_as_dict(reading: BookReading, title: str | None) -> dict[str, Any]:
    result: dict[str, Any] = {
        "key": str(reading.key),
        "title": title,
        "device_sessions_summary": asdict(reading.device_sessions_summary),
    }
    for item in fields(reading):
        if item.name != "key":
            result[item.name] = [
                {
                    name: value
                    for name, value in asdict(record).items()
                    if name not in _OMITTED_RECORD_FIELDS
                }
                for record in getattr(reading, item.name)
            ]
    return result


def _json_default(value: object) -> str:
    if isinstance(value, date):
        return value.isoformat()
    raise TypeError(f"cannot serialize {type(value).__name__}")


__all__ = ["main"]
