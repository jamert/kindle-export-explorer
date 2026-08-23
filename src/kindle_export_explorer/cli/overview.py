"""Command-line overview of Kindle books, acquisitions, and reading."""

from __future__ import annotations

import csv
import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

import click

from ..acquisitions import BookAcquisition, reconstruct_acquisitions
from ..books import HEADERS, BookCanonical, CanonicalKey, ExportError, reconstruct_books
from ..formatting import format_datetime
from ..paths import resolve_export_path
from ..reading import BookReading, reconstruct_reading
from .utils import identifier_predicate, keys_predicate, parse_identifiers


_OVERVIEW_HEADERS = (
    *HEADERS,
    "acquired_sample",
    "acquired_book",
    "reading_ds_start",
    "reading_ds_end",
    "reading_ds_total_reading_humanized",
    "reading_ws_start",
    "reading_ws_end",
    "reading_ws_dates_unique",
)


@click.command(context_settings={"help_option_names": ["-h", "--help"]})
@click.option(
    "--json",
    "json_lines",
    is_flag=True,
    help="Write one JSON object per line instead of TSV.",
)
@click.option(
    "--include",
    metavar="ID[,ID...]",
    help="Only emit records matching these comma-separated keys, ASINs, or document IDs.",
)
@click.option(
    "--exclude",
    metavar="ID[,ID...]",
    help="Omit records matching these comma-separated keys, ASINs, or document IDs.",
)
@click.argument(
    "export_path",
    required=False,
    type=click.Path(path_type=Path, exists=True, resolve_path=True),
)
def overview(
    export_path: Path | None,
    json_lines: bool,
    include: str | None,
    exclude: str | None,
) -> None:
    """Print an overview from directory or ZIP EXPORT_PATH.

    EXPORT_PATH defaults to the KINDLE_EXPORT_PATH environment variable.
    """
    try:
        export_path = resolve_export_path(export_path)
    except ValueError as exc:
        raise click.UsageError(str(exc)) from exc
    included_ids = parse_identifiers(include, "--include")
    excluded_ids = parse_identifiers(exclude, "--exclude")
    predicate = identifier_predicate(included_ids, excluded_ids)
    try:
        books = reconstruct_books(
            export_path,
            show_default=False,
            show_samples=True,
            source="kindle",
            predicate=predicate,
        )
        join_predicate = keys_predicate({book.key for book in books})
        acquisitions = {
            item.key: item
            for item in reconstruct_acquisitions(
                export_path,
                predicate=join_predicate,
            )
        }
        readings = {
            item.key: item
            for item in reconstruct_reading(
                export_path,
                predicate=join_predicate,
            )
        }
    except ExportError as exc:
        raise click.ClickException(str(exc)) from exc

    books.sort(key=lambda book: _acquisition_sort_key(book, acquisitions))
    if json_lines:
        _write_json_lines(books, acquisitions, readings)
    else:
        _write_tsv(books, acquisitions, readings)


# Output details

def _write_json_lines(
    books: list[BookCanonical],
    acquisitions: dict[CanonicalKey, BookAcquisition],
    readings: dict[CanonicalKey, BookReading],
) -> None:
    for book in books:
        record = book.as_dict()
        record.update(
            _overview_values(
                acquisitions.get(book.key),
                readings.get(book.key),
            )
        )
        click.echo(json.dumps(record, ensure_ascii=False))


def _write_tsv(
    books: list[BookCanonical],
    acquisitions: dict[CanonicalKey, BookAcquisition],
    readings: dict[CanonicalKey, BookReading],
) -> None:
    writer = csv.writer(sys.stdout, dialect="excel-tab", lineterminator="\n")
    writer.writerow(_OVERVIEW_HEADERS)
    for book in books:
        values = _overview_values(
            acquisitions.get(book.key),
            readings.get(book.key),
        )
        writer.writerow(
            [
                *book.as_row(),
                *(
                    "" if values[header] is None else values[header]
                    for header in _OVERVIEW_HEADERS[len(HEADERS) :]
                ),
            ]
        )


def _overview_values(
    acquisition: BookAcquisition | None,
    reading: BookReading | None,
) -> dict[str, Any]:
    acquired_sample = acquisition.acquired_sample if acquisition else None
    acquired_book = acquisition.acquired_book if acquisition else None
    device_summary = reading.device_sessions_summary if reading else None
    whispersync_summary = reading.whispersync_record_summary if reading else None
    return {
        "acquired_sample": _timestamp(acquired_sample),
        "acquired_book": _timestamp(acquired_book),
        "reading_ds_start": _timestamp(
            device_summary.start if device_summary else None
        ),
        "reading_ds_end": _timestamp(device_summary.end if device_summary else None),
        "reading_ds_total_reading_humanized": (
            device_summary.total_reading_humanized if device_summary else None
        ),
        "reading_ws_start": _timestamp(
            whispersync_summary.start if whispersync_summary else None
        ),
        "reading_ws_end": _timestamp(
            whispersync_summary.end if whispersync_summary else None
        ),
        "reading_ws_dates_unique": (
            whispersync_summary.dates_unique if whispersync_summary else 0
        ),
    }


def _acquisition_sort_key(
    book: BookCanonical,
    acquisitions: dict[CanonicalKey, BookAcquisition],
) -> tuple[bool, str, str]:
    acquisition = acquisitions.get(book.key)
    acquired = (
        acquisition.acquired_book or acquisition.acquired_sample
        if acquisition
        else None
    )
    return acquired is None, _timestamp(acquired) or "", str(book.key)


def _timestamp(value: datetime | None) -> str | None:
    return format_datetime(value) if value else None


__all__ = ["overview"]
