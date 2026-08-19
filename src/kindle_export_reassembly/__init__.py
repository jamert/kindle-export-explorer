"""Command-line interface for Kindle export reassembly."""

from __future__ import annotations

import csv
import json
import sys
from pathlib import Path

import click

from .books import Book, ExportError, HEADERS, raw_headers, reconstruct_books


_RAW_SYNTHETIC_HEADERS = (
    "synthetic->key",
    "synthetic->source",
    "synthetic->is_sample",
)
_IDENTIFIER_ERROR = "provide at least one key, ASIN, or document ID"


def _parse_identifiers(value: str | None, option: str) -> set[str] | None:
    if value is None:
        return None
    identifiers = {
        identifier.strip().casefold()
        for identifier in value.split(",")
        if identifier.strip()
    }
    if not identifiers:
        raise click.BadParameter(_IDENTIFIER_ERROR, param_hint=option)
    return identifiers


def _book_identifiers(book: Book) -> set[str]:
    return {
        identifier.casefold()
        for identifier in (book.key, book.asin, book.document_id)
        if identifier
    }


def _select_books(
    books: list[Book],
    included: set[str] | None,
    excluded: set[str] | None,
) -> list[Book]:
    selected = books
    if included is not None:
        selected = [book for book in selected if _book_identifiers(book) & included]
    if excluded is not None:
        selected = [book for book in selected if not (_book_identifiers(book) & excluded)]
    return selected


def _write_json_lines(books: list[Book], raw: bool, extra_headers: list[str]) -> None:
    for book in books:
        record = book.as_raw_dict(extra_headers) if raw else book.as_dict()
        click.echo(json.dumps(record, ensure_ascii=False))


def _write_tsv(books: list[Book], raw: bool, extra_headers: list[str]) -> None:
    writer = csv.writer(sys.stdout, dialect="excel-tab", lineterminator="\n")
    if raw:
        writer.writerow([*_RAW_SYNTHETIC_HEADERS, *extra_headers])
        writer.writerows(book.as_raw_row(extra_headers) for book in books)
    else:
        writer.writerow(HEADERS)
        writer.writerows(book.as_row() for book in books)


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
    help="Select books by source.",
)
@click.option(
    "--raw",
    is_flag=True,
    help="Append all connected source fields available for the selected books.",
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
    "export_directory",
    type=click.Path(path_type=Path, exists=True, file_okay=False, resolve_path=True),
)
def main(
    export_directory: Path,
    show_default: bool,
    show_samples: bool,
    source: str,
    raw: bool,
    json_lines: bool,
    include: str | None,
    exclude: str | None,
) -> None:
    """Print book metadata reconstructed from EXPORT_DIRECTORY as TSV."""
    requested_ids = _parse_identifiers(include, "--include")

    try:
        books = reconstruct_books(
            export_directory,
            # Explicit IDs override all category filters. Exclusion is still
            # applied below and therefore always wins.
            show_default=show_default,
            show_samples=True if requested_ids is not None else show_samples,
            source="all" if requested_ids is not None else source,
        )
    except ExportError as exc:
        raise click.ClickException(str(exc)) from exc

    excluded_ids = _parse_identifiers(exclude, "--exclude")
    books = _select_books(books, requested_ids, excluded_ids)
    extra_headers = raw_headers(books) if raw else []
    if json_lines:
        _write_json_lines(books, raw, extra_headers)
    else:
        _write_tsv(books, raw, extra_headers)


__all__ = ["main", "reconstruct_books"]
