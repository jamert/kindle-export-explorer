"""Shared command-line filtering helpers."""

from __future__ import annotations

import click

from ..books import BookCanonical


_IDENTIFIER_ERROR = "provide at least one key, ASIN, or document ID"


def parse_identifiers(value: str | None, option: str) -> set[str] | None:
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


def select_books(
    books: list[BookCanonical],
    included: set[str] | None,
    excluded: set[str] | None,
) -> list[BookCanonical]:
    selected = books
    if included is not None:
        selected = [book for book in selected if _book_identifiers(book) & included]
    if excluded is not None:
        selected = [
            book for book in selected if not (_book_identifiers(book) & excluded)
        ]
    return selected


def _book_identifiers(book: BookCanonical) -> set[str]:
    return {
        identifier.casefold()
        for identifier in (str(book.key), book.asin, book.document_id)
        if identifier
    }
