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
    "--asin",
    metavar="ASIN[,ASIN...]",
    help="Only emit records with one of these comma-separated ASINs.",
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
    asin: str | None,
) -> None:
    """Print book metadata reconstructed from EXPORT_DIRECTORY as TSV."""
    try:
        books = reconstruct_books(
            export_directory,
            show_default=show_default,
            show_samples=show_samples,
            source=source,
        )
    except ExportError as exc:
        raise click.ClickException(str(exc)) from exc

    if asin is not None:
        requested_asins = {
            value.strip().casefold() for value in asin.split(",") if value.strip()
        }
        if not requested_asins:
            raise click.BadParameter(
                "provide at least one ASIN", param_hint="--asin"
            )
        books = [book for book in books if book.asin.casefold() in requested_asins]

    extra_headers = raw_headers(books) if raw else []
    if json_lines:
        for book in books:
            click.echo(json.dumps(book.as_dict(extra_headers), ensure_ascii=False))
        return

    writer = csv.writer(sys.stdout, dialect="excel-tab", lineterminator="\n")
    writer.writerow([*HEADERS, *extra_headers])
    writer.writerows(book.as_row(extra_headers) for book in books)


__all__ = ["main", "reconstruct_books"]
