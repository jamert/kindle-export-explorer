"""Shared command-line filtering helpers."""

from __future__ import annotations

import click

from ..books import CanonicalKey, CanonicalKeyPredicate


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


def identifier_predicate(
    included: set[str] | None,
    excluded: set[str] | None,
) -> CanonicalKeyPredicate:
    """Convert CLI identifier options into a canonical-key predicate."""

    def selected(key: CanonicalKey) -> bool:
        identifiers = _key_identifiers(key)
        return (
            (included is None or bool(identifiers & included))
            and (excluded is None or not identifiers & excluded)
        )

    return selected


def keys_predicate(keys: set[CanonicalKey]) -> CanonicalKeyPredicate:
    """Select only records which can participate in a later key-based join."""
    return keys.__contains__


def _key_identifiers(key: CanonicalKey) -> set[str]:
    return {
        identifier.casefold()
        for identifier in (str(key), key.asin, key.document_id)
        if identifier
    }
