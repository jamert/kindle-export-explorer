"""Reconstruct book metadata from an Amazon Kindle data export."""

from __future__ import annotations

import csv
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Iterator


_MISSING = {"", "not available", "not applicable", "null", "none"}
_DEFAULT_ORIGIN_TYPES = {"kindledictionary", "kindleuserguide"}


class ExportError(ValueError):
    """Raised when a directory is not a recognizable Kindle export."""


def clean(value: object) -> str:
    if value is None:
        return ""
    result = str(value).strip()
    return "" if result.casefold() in _MISSING else result


def clean_item_asin(value: object) -> str:
    result = clean(value)
    prefix = "urn:collection:1:asin-"
    if result.casefold().startswith(prefix):
        return result[len(prefix) :]
    return result


@dataclass
class Book:
    asin: str = ""
    document_id: str = ""
    title: str = ""
    authors: set[str] = field(default_factory=set)
    genres: set[str] = field(default_factory=set)
    series_title: str = ""
    series_position: str = ""
    is_default_content: bool = field(default=False, repr=False)
    _title_rank: int = field(default=-1, repr=False)
    _series_rank: int = field(default=-1, repr=False)

    @property
    def key(self) -> str:
        return self.asin or f"document:{self.document_id}"

    def set_title(self, value: object, rank: int) -> None:
        title = clean(value)
        if title and rank >= self._title_rank:
            self.title = title
            self._title_rank = rank

    def set_series(self, title: object, position: object, rank: int) -> None:
        series_title = clean(title)
        if series_title and rank >= self._series_rank:
            self.series_title = series_title
            self.series_position = clean(position)
            self._series_rank = rank

    def as_row(self) -> list[str]:
        return [
            self.asin,
            self.document_id,
            self.title,
            "; ".join(sorted(self.authors, key=str.casefold)),
            "; ".join(sorted(self.genres, key=str.casefold)),
            self.series_title,
            self.series_position,
        ]


HEADERS = [
    "asin",
    "document_id",
    "title",
    "authors",
    "genres",
    "series_title",
    "series_position",
]


def _csv_rows(paths: Iterable[Path]) -> Iterator[dict[str, str]]:
    for path in paths:
        try:
            with path.open(encoding="utf-8-sig", newline="") as stream:
                yield from csv.DictReader(stream)
        except (OSError, UnicodeError, csv.Error) as exc:
            raise ExportError(f"cannot read {path}: {exc}") from exc


def _files_named(root: Path, fragment: str, suffix: str = ".csv") -> list[Path]:
    needle = fragment.casefold()
    return sorted(
        path
        for path in root.rglob(f"*{suffix}")
        if needle in path.name.casefold()
    )


def reconstruct_books(root: Path, *, show_default: bool = False) -> list[Book]:
    """Return deduplicated, intrinsic book metadata found under *root*.

    Kindle-supplied dictionaries and user guides are omitted unless
    ``show_default`` is true. Activity fields (reading dates, progress, sessions,
    annotations, and account state) are deliberately not copied into the result.
    """
    root = root.expanduser()
    if not root.is_dir():
        raise ExportError(f"not a directory: {root}")

    books: dict[str, Book] = {}
    recognized = False

    def get(asin: object = "", document_id: object = "") -> Book | None:
        asin_value = clean(asin)
        document_value = clean(document_id)
        if asin_value.casefold() == "invalid-asin":
            asin_value = ""
        if document_value.casefold() == "invalid-asin":
            document_value = ""
        if not asin_value and not document_value:
            return None
        key = asin_value or f"document:{document_value}"
        book = books.get(key)
        if book is None:
            book = books[key] = Book(asin=asin_value, document_id=document_value)
        return book

    # Digital ownership is the broadest source for Kindle books and samples.
    ownership_files = _files_named(root, "Digital.Content.Ownership", ".json")
    if ownership_files:
        recognized = True
    for path in ownership_files:
        try:
            data = json.loads(path.read_text(encoding="utf-8-sig"))
            resource = data.get("resource", {})
        except (OSError, UnicodeError, json.JSONDecodeError, AttributeError) as exc:
            raise ExportError(f"cannot read {path}: {exc}") from exc
        book = get(resource.get("ASIN"))
        if book:
            book.set_title(resource.get("Product Name"), 30)
            origins = {
                clean(right.get("origin", {}).get("originType")).casefold()
                for right in data.get("rights", [])
                if isinstance(right, dict) and isinstance(right.get("origin"), dict)
            }
            book.is_default_content = bool(origins & _DEFAULT_ORIGIN_TYPES)

    # Unified Library Index identifies actual library items. Exclude rows which
    # only describe wish-list/not-interested/customer-metadata activity.
    relationship_paths = _files_named(root, "CustomerRelationshipIndex")
    if relationship_paths:
        recognized = True
    relationship_rows = list(_csv_rows(relationship_paths))
    owner_types = {"item owner", "sample owner"}
    for row in relationship_rows:
        if clean(row.get("Resource Type")).casefold() != "item":
            continue
        if clean(row.get("Ownership Type")).casefold() not in owner_types:
            continue
        book = get(row.get("ASIN"))
        if book:
            book.set_title(row.get("Product Name"), 50)
            book.set_series(row.get("Series Title"), row.get("Position In Collection"), 10)

    # Product-bearing export tables recover titles whose current library right
    # is absent. Their behavioral fields are intentionally ignored.
    title_sources = [
        ("whispersync.csv", "ASIN", "Product Name", 20),
        ("ContentUpdates", "ASIN", "Product Name", 25),
        ("ManualContentUpdates", "ASIN", "Product Name", 25),
        ("AnnotationUpdates", "ASIN", "Product Name", 25),
        ("BookRelation.csv", "ASIN", "Product Name", 35),
    ]
    for fragment, id_column, title_column, rank in title_sources:
        paths = _files_named(root, fragment)
        if paths:
            recognized = True
        for row in _csv_rows(paths):
            if not clean(row.get(title_column)):
                continue
            book = get(row.get(id_column))
            if book:
                book.set_title(row.get(title_column), rank)

    # Reading Insights contains a title alongside its identifier. We retain only
    # those two book attributes, never completion/session information.
    insight_paths = _files_named(root, "reading-insights-sessions_with_adjustments")
    completed_paths = _files_named(root, "UserUniqueTitlesCompleted")
    if insight_paths or completed_paths:
        recognized = True
    for row in _csv_rows(insight_paths):
        if clean(row.get("product_name")):
            book = get(row.get("ASIN"), row.get("personal_document_id"))
            if book:
                book.set_title(row.get("product_name"), 40)
    for row in _csv_rows(completed_paths):
        encoded = clean(row.get("asin_date_and_content_type"))
        asin = encoded.split("_", 1)[0] if encoded else ""
        if clean(row.get("product_name")):
            book = get(asin, row.get("personal_document_id"))
            if book:
                book.set_title(row.get("product_name"), 40)

    # Personal documents have no ASIN, so preserve their Amazon document ID.
    document_paths = _files_named(root, "DocumentMetadata")
    if document_paths:
        recognized = True
    for row in _csv_rows(document_paths):
        book = get(document_id=row.get("DocumentId"))
        if book:
            book.set_title(row.get("Title"), 50)

    # The Saga table provides explicit item-to-series metadata.
    saga_paths = _files_named(root, "CollectionRightsDatastore")
    if saga_paths:
        recognized = True
    for row in _csv_rows(saga_paths):
        if clean(row.get("record-type")).casefold() != "item":
            continue
        item_asin = clean_item_asin(row.get("item-ASIN"))
        # Some rows connect nested series and have no item title; they are not books.
        if not clean(row.get("item-product-name")) and item_asin not in books:
            continue
        book = get(item_asin)
        if book:
            book.set_title(row.get("item-product-name"), 45)
            book.set_series(row.get("series-product-name"), row.get("item-position-in-series"), 50)

    # Enrich established books from metadata-only ULI relations. These files do
    # not seed records, preventing recommendations and wish-list items leaking in.
    for row in _csv_rows(_files_named(root, "CustomerAuthorNameRelationship")):
        book = books.get(clean(row.get("ASIN")))
        author = clean(row.get("Author Name"))
        if book and author:
            book.authors.add(author)
    for row in _csv_rows(_files_named(root, "CustomerGenres")):
        book = books.get(clean(row.get("ASIN")))
        genre = clean(row.get("Genre"))
        if book and genre:
            book.genres.add(genre)

    if not recognized:
        raise ExportError(f"no recognized Kindle export files found in {root}")

    result = books.values()
    if not show_default:
        result = (book for book in result if not book.is_default_content)
    return sorted(result, key=lambda book: (book.title.casefold(), book.key))
