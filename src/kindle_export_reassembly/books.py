"""Reconstruct book metadata from an Amazon Kindle data export."""

from __future__ import annotations

import csv
import json
import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterator


_MISSING = {"", "not available", "not applicable", "null", "none"}
_DEFAULT_ORIGIN_TYPES = {"kindledictionary", "kindleuserguide"}
# These source fields are represented by authoritative canonical output columns.
_CANONICAL_RAW_FIELDS = {
    "asin",
    "author name",
    "documentid",
    "genre",
    "item-asin",
    "item-position-in-series",
    "item-product-name",
    "position in collection",
    "product name",
    "series title",
    "series-product-name",
    "title",
}


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
    source: str = "kindle"
    is_sample: bool = False
    is_default_content: bool = field(default=False, repr=False)
    raw_fields: dict[str, set[str]] = field(default_factory=dict, repr=False)
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

    def add_raw(
        self,
        source_path: str,
        values: Mapping[str, object],
        fields: Iterable[str] | None = None,
    ) -> None:
        """Retain non-empty source values with their exact file provenance."""
        names = fields if fields is not None else values.keys()
        for name in names:
            # Canonical columns use an explicit source-precedence policy. Do not
            # duplicate those values in raw output. Saga's series-ASIN is retained
            # because it identifies a different entity.
            if name.rsplit(".", 1)[-1].casefold() in _CANONICAL_RAW_FIELDS:
                continue
            value = clean(values.get(name))
            if value:
                self.raw_fields.setdefault(f"{source_path}->{name}", set()).add(value)

    def as_dict(
        self, raw_headers: Iterable[str] = (), *, synthetic_prefix: bool = False
    ) -> dict[str, object]:
        prefix = "synthetic->" if synthetic_prefix else ""
        result: dict[str, object] = {
            f"{prefix}asin": self.asin,
            f"{prefix}document_id": self.document_id,
            f"{prefix}title": self.title,
            f"{prefix}authors": sorted(self.authors, key=str.casefold),
            f"{prefix}genres": sorted(self.genres, key=str.casefold),
            f"{prefix}series_title": self.series_title,
            f"{prefix}series_position": self.series_position,
            f"{prefix}source": self.source,
            f"{prefix}is_sample": self.is_sample,
        }
        result.update(
            {
                header: "; ".join(
                    sorted(self.raw_fields.get(header, ()), key=str.casefold)
                )
                for header in raw_headers
            }
        )
        return result

    def as_row(self, raw_headers: Iterable[str] = ()) -> list[str]:
        row = [
            self.asin,
            self.document_id,
            self.title,
            "; ".join(sorted(self.authors, key=str.casefold)),
            "; ".join(sorted(self.genres, key=str.casefold)),
            self.series_title,
            self.series_position,
            self.source,
            "true" if self.is_sample else "false",
        ]
        row.extend(
            "; ".join(sorted(self.raw_fields.get(header, ()), key=str.casefold))
            for header in raw_headers
        )
        return row


HEADERS = [
    "asin",
    "document_id",
    "title",
    "authors",
    "genres",
    "series_title",
    "series_position",
    "source",
    "is_sample",
]


def raw_headers(books: Iterable[Book]) -> list[str]:
    """Return the sorted union of raw fields present on the selected books."""
    return sorted({name for book in books for name in book.raw_fields}, key=str.casefold)


_NUMBERED_SHARD = re.compile(r"^(?P<base>.+)\.(?P<number>\d+)(?P<extension>\.[^.]+)$")


def normalize_sharded_path(root: Path, path: Path) -> str:
    """Return an export-relative provenance path, collapsing real shard groups.

    A numbered file is considered a shard only when its directory contains another
    file with the same base and extension but a different numeric suffix. Singleton
    versioned files therefore retain their exact names.
    """
    match = _NUMBERED_SHARD.match(path.name)
    if match:
        pattern = f"{match.group('base')}.*{match.group('extension')}"
        matching_siblings = (
            sibling
            for sibling in path.parent.glob(pattern)
            if (sibling_match := _NUMBERED_SHARD.match(sibling.name))
            and sibling_match.group("base") == match.group("base")
            and sibling_match.group("extension") == match.group("extension")
        )
        if sum(1 for _ in matching_siblings) > 1:
            path = path.with_name(f"shard{match.group('extension')}")
    return path.relative_to(root).as_posix()


def _csv_rows(
    root: Path, paths: Iterable[Path]
) -> Iterator[tuple[str, dict[str, str]]]:
    for path in paths:
        try:
            with path.open(encoding="utf-8-sig", newline="") as stream:
                source_path = normalize_sharded_path(root, path)
                for row in csv.DictReader(stream):
                    yield source_path, row
        except (OSError, UnicodeError, csv.Error) as exc:
            raise ExportError(f"cannot read {path}: {exc}") from exc


def _files_named(root: Path, fragment: str, suffix: str = ".csv") -> list[Path]:
    needle = fragment.casefold()
    return sorted(
        path
        for path in root.rglob(f"*{suffix}")
        if needle in path.name.casefold()
    )


def reconstruct_books(
    root: Path,
    *,
    show_default: bool = False,
    show_samples: bool = False,
    source: str = "kindle",
) -> list[Book]:
    """Return deduplicated, intrinsic book metadata found under *root*.

    ``source`` selects Kindle content, print books, or ``all``. Samples are omitted
    unless ``show_samples`` is true. Kindle-supplied dictionaries and user guides
    are omitted unless ``show_default`` is true. Activity fields (reading dates,
    progress, sessions, annotations, and account state) are deliberately not copied.
    """
    if source not in {"kindle", "print", "all"}:
        raise ValueError(f"invalid source: {source}")
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
            source_path = normalize_sharded_path(root, path)
            book.source = "kindle"
            book.set_title(resource.get("Product Name"), 30)
            book.add_raw(
                source_path,
                {f"resource.{name}": value for name, value in resource.items()},
            )
            for right in data.get("rights", []):
                if not isinstance(right, dict):
                    continue
                book.add_raw(
                    source_path,
                    {
                        "rights.rightType": right.get("rightType"),
                        "rights.acquiredDate": right.get("acquiredDate"),
                    },
                )
                origin = right.get("origin", {})
                if isinstance(origin, dict):
                    book.add_raw(
                        source_path,
                        {
                            f"rights.origin.{name}": value
                            for name, value in origin.items()
                        },
                    )
            origins = {
                clean(right.get("origin", {}).get("originType")).casefold()
                for right in data.get("rights", [])
                if isinstance(right, dict) and isinstance(right.get("origin"), dict)
            }
            book.is_sample = book.is_sample or (
                clean(resource.get("resourceType")).casefold() == "kindleebooksample"
                or "sample" in origins
            )
            book.is_default_content = book.is_default_content or bool(
                origins & _DEFAULT_ORIGIN_TYPES
            )

    # Unified Library Index identifies actual library items. Exclude rows which
    # only describe wish-list/not-interested/customer-metadata activity.
    relationship_paths = _files_named(root, "CustomerRelationshipIndex")
    if relationship_paths:
        recognized = True
    relationship_rows = list(_csv_rows(root, relationship_paths))
    owner_types = {"item owner", "sample owner"}
    for source_path, row in relationship_rows:
        if clean(row.get("Resource Type")).casefold() != "item":
            continue
        if clean(row.get("Ownership Type")).casefold() not in owner_types:
            continue
        asin = clean(row.get("ASIN"))
        existing = books.get(asin)
        book = get(asin)
        if book:
            # ULI includes physical Amazon purchases. Kindle ownership evidence
            # takes precedence; an item found only in ULI is a print book.
            if existing is None:
                book.source = "print"
            book.add_raw(
                source_path,
                row,
                (
                    "Product Name",
                    "ASIN",
                    "Resource Type",
                    "Our Price",
                    "Sortable Title",
                    "Sortable Author Name",
                    "Series Author",
                    "Series Title",
                    "Relation Type",
                    "Position In Collection",
                    "Marketplace",
                    "Relationship Creation Date",
                ),
            )
            book.set_title(row.get("Product Name"), 50)
            if clean(row.get("Ownership Type")).casefold() == "sample owner":
                book.is_sample = True
            book.set_series(row.get("Series Title"), row.get("Position In Collection"), 10)

    # BookRelation contains intrinsic item-to-series catalog relations. Its
    # acquisition timestamp is deliberately ignored.
    book_relation_paths = _files_named(root, "BookRelation.csv")
    if book_relation_paths:
        recognized = True
    for source_path, row in _csv_rows(root, book_relation_paths):
        if not clean(row.get("Product Name")):
            continue
        book = get(row.get("ASIN"))
        if book:
            book.source = "kindle"
            book.add_raw(source_path, row, ("ASIN", "Product Name"))
            book.set_title(row.get("Product Name"), 35)

    # Personal documents have no ASIN, so preserve their Amazon document ID.
    document_paths = _files_named(root, "DocumentMetadata")
    if document_paths:
        recognized = True
    for source_path, row in _csv_rows(root, document_paths):
        book = get(document_id=row.get("DocumentId"))
        if book:
            book.source = "kindle"
            book.add_raw(
                source_path,
                row,
                tuple(name for name in row if name != "HasBeenDeleted"),
            )
            book.set_title(row.get("Title"), 50)

    # The Saga table provides explicit item-to-series metadata.
    saga_paths = _files_named(root, "CollectionRightsDatastore")
    if saga_paths:
        recognized = True
    for source_path, row in _csv_rows(root, saga_paths):
        if clean(row.get("record-type")).casefold() != "item":
            continue
        item_asin = clean_item_asin(row.get("item-ASIN"))
        # Some rows connect nested series and have no item title; they are not books.
        if not clean(row.get("item-product-name")) and item_asin not in books:
            continue
        book = get(item_asin)
        if book:
            book.source = "kindle"
            book.add_raw(
                source_path,
                row,
                (
                    "record-type",
                    "series-ASIN",
                    "series-product-name",
                    "item-ASIN",
                    "item-product-name",
                    "item-position-in-series",
                ),
            )
            book.set_title(row.get("item-product-name"), 45)
            book.set_series(row.get("series-product-name"), row.get("item-position-in-series"), 50)

    # Enrich established books from metadata-only ULI relations. These files do
    # not seed records, preventing recommendations and wish-list items leaking in.
    for source_path, row in _csv_rows(
        root, _files_named(root, "CustomerAuthorNameRelationship")
    ):
        book = books.get(clean(row.get("ASIN")))
        author = clean(row.get("Author Name"))
        if book and author:
            book.authors.add(author)
            book.add_raw(source_path, row)
    for source_path, row in _csv_rows(
        root, _files_named(root, "CustomerAuthorIdRelationship")
    ):
        book = books.get(clean(row.get("ASIN")))
        if book:
            book.add_raw(source_path, row)
    for source_path, row in _csv_rows(root, _files_named(root, "CustomerGenres")):
        book = books.get(clean(row.get("ASIN")))
        genre = clean(row.get("Genre"))
        if book and genre:
            book.genres.add(genre)
            book.add_raw(source_path, row)
    for source_path, row in _csv_rows(
        root, _files_named(root, "CustomerRelationshipTypes")
    ):
        book = books.get(clean(row.get("ASIN")))
        if book and clean(row.get("Ownership Type")).casefold() in owner_types:
            book.add_raw(source_path, row)
    book_tag_groups = {"author", "catalog", "genre", "media-concept-node"}
    for source_path, row in _csv_rows(root, _files_named(root, "CustomerTags")):
        book = books.get(clean(row.get("ASIN")))
        if book and clean(row.get("Tag Source Group")).casefold() in book_tag_groups:
            book.add_raw(
                source_path,
                row,
                (
                    "Tag Name",
                    "Tag Scope",
                    "Tag Source Group",
                    "Tag Source Subgroup",
                    "Image URL",
                ),
            )

    if not recognized:
        raise ExportError(f"no recognized Kindle export files found in {root}")

    result = books.values()
    if not show_default:
        result = (book for book in result if not book.is_default_content)
    if not show_samples:
        result = (book for book in result if not book.is_sample)
    if source != "all":
        result = (book for book in result if book.source == source)
    return sorted(result, key=lambda book: (book.title.casefold(), book.key))
