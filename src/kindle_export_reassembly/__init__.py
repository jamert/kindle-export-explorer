"""Command-line interface for Kindle export reassembly."""

from __future__ import annotations

import csv
import sys
from pathlib import Path

import click

from .books import ExportError, HEADERS, reconstruct_books


@click.command(context_settings={"help_option_names": ["-h", "--help"]})
@click.option(
    "--show-default",
    is_flag=True,
    help="Include Kindle-supplied dictionaries and user guides.",
)
@click.option("--show-samples", is_flag=True, help="Include book samples.")
@click.argument(
    "export_directory",
    type=click.Path(path_type=Path, exists=True, file_okay=False, resolve_path=True),
)
def main(export_directory: Path, show_default: bool, show_samples: bool) -> None:
    """Print book metadata reconstructed from EXPORT_DIRECTORY as TSV."""
    try:
        books = reconstruct_books(
            export_directory,
            show_default=show_default,
            show_samples=show_samples,
        )
    except ExportError as exc:
        raise click.ClickException(str(exc)) from exc

    writer = csv.writer(sys.stdout, dialect="excel-tab", lineterminator="\n")
    writer.writerow(HEADERS)
    writer.writerows(book.as_row() for book in books)


__all__ = ["main", "reconstruct_books"]
