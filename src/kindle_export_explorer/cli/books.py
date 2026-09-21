"""Canonical-book command implementation."""

from __future__ import annotations

import csv
import json
import sys
from pathlib import Path

import click

from ..acquisitions import BookAcquisition, reconstruct_acquisitions
from ..books import (
    EXTRA_HEADERS,
    HEADERS,
    BookCanonical,
    CanonicalKey,
    ExportError,
    reconstruct_books,
)
from ..formatting import format_datetime
from ..paths import resolve_export_path
from .utils import identifier_predicate, keys_predicate, parse_identifiers

_ACQUISITION_HEADERS = ("acquired_sample", "acquired_book")


# Command overview


@click.command(context_settings={"help_option_names": ["-h", "--help"]})
@click.option(
    "--show-default",
    is_flag=True,
    help="Include Kindle-supplied dictionaries and user guides.",
)
@click.option("--show-samples", is_flag=True, help="Include book samples.")
@click.option(
    "--source",
    type=click.Choice(["kindle", "print", "all"], case_sensitive=False),
    default="kindle",
    show_default=True,
    help="Select books by ownership source.",
)
@click.option(
    "--extra",
    is_flag=True,
    help="Include series, genres, and book/series links.",
)
@click.option(
    "--acquisition",
    is_flag=True,
    help="Include sample and book acquisition timestamps.",
)
@click.option(
    "--jsonl",
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
def books(
    export_path: Path | None,
    show_default: bool,
    show_samples: bool,
    source: str,
    extra: bool,
    acquisition: bool,
    json_lines: bool,
    include: str | None,
    exclude: str | None,
) -> None:
    """Print books from directory or ZIP EXPORT_PATH.

    EXPORT_PATH defaults to the KINDLE_EXPORT_PATH environment variable.
    """
    try:
        export_path = resolve_export_path(export_path)
    except ValueError as exc:
        raise click.UsageError(str(exc)) from exc
    requested_ids = parse_identifiers(include, "--include")
    excluded_ids = parse_identifiers(exclude, "--exclude")
    predicate = identifier_predicate(requested_ids, excluded_ids)

    try:
        books = reconstruct_books(
            export_path,
            # Explicit IDs override all category filters. Exclusion is part of
            # the predicate and therefore still always wins.
            show_default=True if requested_ids is not None else show_default,
            show_samples=True if requested_ids is not None else show_samples,
            source="all" if requested_ids is not None else source,
            predicate=predicate,
        )
        acquisition_by_key = (
            {
                item.key: item
                for item in reconstruct_acquisitions(
                    export_path,
                    predicate=keys_predicate({book.key for book in books}),
                )
            }
            if acquisition
            else None
        )
    except ExportError as exc:
        raise click.ClickException(str(exc)) from exc

    if json_lines:
        _write_json_lines(books, extra, acquisition_by_key)
    else:
        _write_tsv(books, extra, acquisition_by_key)


# CLI implementation details


def _write_json_lines(
    books: list[BookCanonical],
    extra: bool,
    acquisition_by_key: dict[CanonicalKey, BookAcquisition] | None,
) -> None:
    for book in books:
        record = book.as_dict(extra=extra)
        if acquisition_by_key is not None:
            record.update(_acquisition_values(acquisition_by_key.get(book.key)))
        click.echo(json.dumps(record, ensure_ascii=False))


def _write_tsv(
    books: list[BookCanonical],
    extra: bool,
    acquisition_by_key: dict[CanonicalKey, BookAcquisition] | None,
) -> None:
    writer = csv.writer(sys.stdout, dialect="excel-tab", lineterminator="\n")
    acquisition_headers = _ACQUISITION_HEADERS if acquisition_by_key is not None else ()
    writer.writerow([*HEADERS, *(EXTRA_HEADERS if extra else []), *acquisition_headers])
    for book in books:
        row = book.as_row(extra=extra)
        if acquisition_by_key is not None:
            values = _acquisition_values(acquisition_by_key.get(book.key))
            row.extend(values[header] or "" for header in _ACQUISITION_HEADERS)
        writer.writerow(row)


def _acquisition_values(
    acquisition: BookAcquisition | None,
) -> dict[str, str | None]:
    acquired_sample = acquisition.acquired_sample if acquisition else None
    acquired_book = acquisition.acquired_book if acquisition else None
    return {
        "acquired_sample": format_datetime(acquired_sample)
        if acquired_sample
        else None,
        "acquired_book": format_datetime(acquired_book) if acquired_book else None,
    }


__all__ = ["books"]
