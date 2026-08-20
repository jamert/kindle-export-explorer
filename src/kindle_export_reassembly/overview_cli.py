"""Command-line overview of Kindle books, acquisitions, and reading."""

from __future__ import annotations

import csv
import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

import click

from .acquisitions import BookAcquisition, reconstruct_acquisitions
from .books import HEADERS, BookCanonical, CanonicalKey, ExportError, reconstruct_books
from .cli_utils import parse_identifiers, select_books
from .formatting import format_datetime
from .reading import BookReading, reconstruct_reading


_OVERVIEW_HEADERS = (
    *HEADERS,
    "acquired_sample",
    "acquired_book",
    "reading_start",
    "reading_end",
    "total_reading_humanized",
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
    "export_directory",
    type=click.Path(path_type=Path, exists=True, file_okay=False, resolve_path=True),
)
def main(
    export_directory: Path,
    json_lines: bool,
    include: str | None,
    exclude: str | None,
) -> None:
    """Print a Kindle book, acquisition, and reading overview."""
    included_ids = parse_identifiers(include, "--include")
    excluded_ids = parse_identifiers(exclude, "--exclude")
    try:
        books = reconstruct_books(
            export_directory,
            show_default=False,
            show_samples=True,
            source="kindle",
        )
        acquisitions = {
            item.key: item for item in reconstruct_acquisitions(export_directory)
        }
        readings = {item.key: item for item in reconstruct_reading(export_directory)}
    except ExportError as exc:
        raise click.ClickException(str(exc)) from exc

    books = select_books(books, included_ids, excluded_ids)
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
                *(values[header] or "" for header in _OVERVIEW_HEADERS[len(HEADERS) :]),
            ]
        )


def _overview_values(
    acquisition: BookAcquisition | None,
    reading: BookReading | None,
) -> dict[str, Any]:
    acquired_sample = acquisition.acquired_sample if acquisition else None
    acquired_book = acquisition.acquired_book if acquisition else None
    summary = reading.device_sessions_summary if reading else None
    return {
        "acquired_sample": _timestamp(acquired_sample),
        "acquired_book": _timestamp(acquired_book),
        "reading_start": _timestamp(summary.start if summary else None),
        "reading_end": _timestamp(summary.end if summary else None),
        "total_reading_humanized": (
            summary.total_reading_humanized if summary else "0m"
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


__all__ = ["main"]
