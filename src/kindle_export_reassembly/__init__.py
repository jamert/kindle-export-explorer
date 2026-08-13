"""Command-line interface for Kindle export reassembly."""

from __future__ import annotations

import csv
import json
import sys
from pathlib import Path

import click

from .books import ExportError, HEADERS, raw_headers, reconstruct_books


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
    help="Only emit records matching these comma-separated ASINs or document IDs.",
)
@click.option(
    "--exclude",
    metavar="ID[,ID...]",
    help="Omit records matching these comma-separated ASINs or document IDs.",
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
    requested_ids: set[str] | None = None
    if include is not None:
        requested_ids = {
            value.strip().casefold() for value in include.split(",") if value.strip()
        }
        if not requested_ids:
            raise click.BadParameter(
                "provide at least one ASIN or document ID", param_hint="--include"
            )

    try:
        books = reconstruct_books(
            export_directory,
            # Explicit IDs override all category filters. Exclusion is still
            # applied below and therefore always wins.
            show_default=True if requested_ids is not None else show_default,
            show_samples=True if requested_ids is not None else show_samples,
            source="all" if requested_ids is not None else source,
        )
    except ExportError as exc:
        raise click.ClickException(str(exc)) from exc

    if requested_ids is not None:
        books = [
            book
            for book in books
            if book.asin.casefold() in requested_ids
            or book.document_id.casefold() in requested_ids
        ]

    if exclude is not None:
        excluded_ids = {
            value.strip().casefold() for value in exclude.split(",") if value.strip()
        }
        if not excluded_ids:
            raise click.BadParameter(
                "provide at least one ASIN or document ID", param_hint="--exclude"
            )
        books = [
            book
            for book in books
            if book.asin.casefold() not in excluded_ids
            and book.document_id.casefold() not in excluded_ids
        ]

    extra_headers = raw_headers(books) if raw else []
    if json_lines:
        for book in books:
            record = book.as_raw_dict(extra_headers) if raw else book.as_dict()
            click.echo(json.dumps(record, ensure_ascii=False))
        return

    writer = csv.writer(sys.stdout, dialect="excel-tab", lineterminator="\n")
    if raw:
        writer.writerow(["synthetic->source", "synthetic->is_sample", *extra_headers])
        writer.writerows(book.as_raw_row(extra_headers) for book in books)
    else:
        writer.writerow(HEADERS)
        writer.writerows(book.as_row() for book in books)


__all__ = ["main", "reconstruct_books"]
