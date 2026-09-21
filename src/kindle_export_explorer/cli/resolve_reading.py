"""Interactive command for manually resolving whether Kindle books were read."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import click

from ..acquisitions import BookAcquisition, reconstruct_acquisitions
from ..books import BookCanonical, ExportError, reconstruct_books
from ..formatting import format_datetime
from ..paths import resolve_export_path
from ..resolutions import (
    ManualResolution,
    ReadStatus,
    ResolutionError,
    load_resolutions,
    save_resolutions,
)
from .utils import identifier_predicate, keys_predicate, parse_identifiers

_RESPONSES = {
    "y": ReadStatus.YES,
    "n": ReadStatus.NO,
    "p": ReadStatus.PARTIALLY,
    "u": ReadStatus.UNKNOWN,
}


@click.command(
    "resolve-reading",
    context_settings={"help_option_names": ["-h", "--help"]},
)
@click.option(
    "--include",
    metavar="ID[,ID...]",
    help=(
        "Resolve only these comma-separated canonical keys, ASINs, or document "
        "IDs, including previously resolved records."
    ),
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
def resolve_reading(
    export_path: Path | None,
    include: str | None,
    exclude: str | None,
) -> None:
    """Interactively record reading status for full Kindle records in EXPORT_PATH.

    Sample-only and Kindle-default records are skipped unless explicitly selected
    with --include.
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
            # Dictionaries, guides, and sample-only records are not full books.
            # Explicit identifiers can still request either category.
            show_default=included_ids is not None,
            show_samples=included_ids is not None,
            source="kindle",
            predicate=predicate,
        )
        selected_keys = {book.key for book in books}
        acquisitions = {
            acquisition.key: acquisition
            for acquisition in reconstruct_acquisitions(
                export_path,
                predicate=keys_predicate(selected_keys),
            )
        }
        resolutions = load_resolutions()
    except (ExportError, ResolutionError) as exc:
        raise click.ClickException(str(exc)) from exc

    matching_books_found = bool(books)
    if included_ids is None:
        books = [book for book in books if str(book.key) not in resolutions]
    if not books:
        message = (
            "all selected Kindle records already have a reading resolution"
            if matching_books_found
            else "no matching Kindle records found"
        )
        click.echo(message)
        return

    books.sort(key=lambda book: str(book.key))
    books.sort(
        key=lambda book: _acquisition_sort_value(acquisitions.get(book.key)),
        reverse=True,
    )

    for book in books:
        acquisition = acquisitions.get(book.key)
        _show_book(book, acquisition)
        status = _prompt_status()
        record = ManualResolution(
            key=str(book.key),
            resolution=status,
            updated_at=datetime.now(UTC),
        )
        resolutions[record.key] = record
        try:
            save_resolutions(resolutions)
        except ResolutionError as exc:
            raise click.ClickException(str(exc)) from exc
        click.echo(f"Saved: {status.value}")


def _show_book(
    book: BookCanonical,
    acquisition: BookAcquisition | None,
) -> None:
    authors = "; ".join(book.authors.names) or "unknown"
    click.echo()
    click.echo(f"Key: {book.key}")
    click.echo(f"Title: {book.title or 'unknown'}")
    click.echo(f"Author: {authors}")
    click.echo(f"Acquired: {_acquisition_text(acquisition) or 'unknown'}")


def _prompt_status() -> ReadStatus:
    while True:
        response = click.prompt(
            "Read? Y(es), N(o), P(artially), or U(nknown)",
            default="U",
        )
        status = _RESPONSES.get(response.strip().casefold())
        if status is not None:
            return status
        click.echo("Response is not recognized. Enter Y, N, P, or U.")


def _acquisition_text(acquisition: BookAcquisition | None) -> str | None:
    acquired = _acquisition_datetime(acquisition)
    return format_datetime(acquired) if acquired is not None else None


def _acquisition_sort_value(acquisition: BookAcquisition | None) -> float:
    acquired = _acquisition_datetime(acquisition)
    if acquired is None:
        return float("-inf")
    if acquired.tzinfo is None:
        acquired = acquired.replace(tzinfo=UTC)
    return acquired.timestamp()


def _acquisition_datetime(
    acquisition: BookAcquisition | None,
) -> datetime | None:
    if acquisition is None:
        return None
    return acquisition.acquired_book or acquisition.acquired_sample


__all__ = ["resolve_reading"]
