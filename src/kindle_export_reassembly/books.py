"""Reconstruct book metadata from an Amazon Kindle data export."""

from __future__ import annotations

import csv
import json
import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterator, TypedDict


_MISSING = {"", "not available", "not applicable", "null", "none"}
_DEFAULT_ORIGIN_TYPES = {"kindledictionary", "kindleuserguide"}
_OWNER_TYPES = {"item owner", "sample owner"}
_SAMPLE_KIND = "sample"
_EBOOK_KIND = "ebook"
_DOCUMENT_KIND = "document"


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


def is_default_personal_document(values: Mapping[str, object]) -> bool:
    """Identify Amazon's Cloud Drive notice without relying on an account ID."""
    provider = clean(values.get("DocumentProvider")).casefold()
    filename = Path(clean(values.get("Filename"))).name.casefold()
    return (
        provider == "amazon cloud drive"
        and filename == "notice from amazon cloud drive.docx"
    )


def _joined(values: Iterable[str]) -> str:
    return "; ".join(sorted(values, key=str.casefold))


class BookRecord(TypedDict):
    key: str
    asin: str
    document_id: str
    title: str
    authors: list[str]
    genres: list[str]
    series_title: str
    series_position: str
    source: str
    is_sample: bool


@dataclass
class Book:
    asin: str = ""
    document_id: str = ""
    content_kind: str = "ebook"
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
        if self.document_id:
            return f"document:{self.document_id}"
        return f"asin:{self.content_kind}:{self.asin}"

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
            value = clean(values.get(name))
            if value:
                self.raw_fields.setdefault(f"{source_path}->{name}", set()).add(value)

    def as_dict(self) -> BookRecord:
        return {
            "key": self.key,
            "asin": self.asin,
            "document_id": self.document_id,
            "title": self.title,
            "authors": sorted(self.authors, key=str.casefold),
            "genres": sorted(self.genres, key=str.casefold),
            "series_title": self.series_title,
            "series_position": self.series_position,
            "source": self.source,
            "is_sample": self.is_sample,
        }

    def as_raw_dict(self, raw_headers: Iterable[str]) -> dict[str, object]:
        result: dict[str, object] = {
            "synthetic->key": self.key,
            "synthetic->source": self.source,
            "synthetic->is_sample": self.is_sample,
        }
        result.update(
            {
                header: _joined(self.raw_fields.get(header, ()))
                for header in raw_headers
            }
        )
        return result

    def as_row(self) -> list[str]:
        record = self.as_dict()
        return [
            record["key"],
            record["asin"],
            record["document_id"],
            record["title"],
            _joined(record["authors"]),
            _joined(record["genres"]),
            record["series_title"],
            record["series_position"],
            record["source"],
            "true" if record["is_sample"] else "false",
        ]

    def as_raw_row(self, raw_headers: Iterable[str]) -> list[str]:
        return [
            self.key,
            self.source,
            "true" if self.is_sample else "false",
            *(_joined(self.raw_fields.get(header, ())) for header in raw_headers),
        ]


HEADERS = [
    "key",
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


class BookCatalog:
    """Deduplicate books by key and index their content variants by ASIN."""

    def __init__(self) -> None:
        self._by_key: dict[str, Book] = {}
        self._by_asin: dict[str, list[Book]] = {}

    def get_or_create(
        self,
        asin: object = "",
        document_id: object = "",
        content_kind: str = _EBOOK_KIND,
    ) -> tuple[Book | None, bool]:
        asin_value = _clean_identifier(asin)
        document_value = _clean_identifier(document_id)
        if not asin_value and not document_value:
            return None, False
        if document_value:
            content_kind = _DOCUMENT_KIND
            key = f"document:{document_value}"
        else:
            key = f"asin:{content_kind}:{asin_value}"
        existing = self._by_key.get(key)
        if existing is not None:
            return existing, False
        book = Book(
            asin=asin_value,
            document_id=document_value,
            content_kind=content_kind,
            is_sample=content_kind == _SAMPLE_KIND,
        )
        self._by_key[key] = book
        # Preserve the existing empty-ASIN bucket used by personal documents.
        self._by_asin.setdefault(asin_value, []).append(book)
        return book, True

    def variants(self, asin: object) -> list[Book]:
        return self._by_asin.get(clean(asin), [])

    def variant(self, asin: object, content_kind: str) -> Book | None:
        return self._by_key.get(f"asin:{content_kind}:{clean(asin)}")

    def values(self) -> Iterable[Book]:
        return self._by_key.values()


def _clean_identifier(value: object) -> str:
    result = clean(value)
    return "" if result.casefold() == "invalid-asin" else result


def raw_headers(books: Iterable[Book]) -> list[str]:
    """Return the sorted union of raw fields present on the selected books."""
    return sorted({name for book in books for name in book.raw_fields}, key=str.casefold)


_NUMBERED_SHARD = re.compile(r"^(?P<base>.+)\.(?P<number>\d+)(?P<extension>\.[^.]+)$")
_VERSIONED_DATASET = re.compile(r"^(?P<base>.+)\.\d+\.\d+$")


def _partitioned_dataset_path(path: Path) -> Path | None:
    """Return a wildcard path when sibling version directories form one dataset."""
    match = _VERSIONED_DATASET.match(path.stem)
    if not match or path.parent.name != path.stem:
        return None
    base = match.group("base")
    matching_files = []
    for directory in path.parent.parent.iterdir():
        directory_match = _VERSIONED_DATASET.match(directory.name)
        if not directory.is_dir() or not directory_match:
            continue
        if directory_match.group("base") != base:
            continue
        candidate = directory / f"{directory.name}{path.suffix}"
        if candidate.is_file():
            matching_files.append(candidate)
    if len(matching_files) < 2:
        return None
    return path.parent.parent / f"{base}.*" / f"*{path.suffix}"


def normalize_sharded_path(root: Path, path: Path) -> str:
    """Return a stable export-relative path for shards and dataset partitions.

    Numbered files in one directory collapse to ``shard.<extension>``. Files whose
    matching version appears in multiple sibling dataset directories collapse to a
    two-level wildcard such as ``Dataset.*/*.csv``. Singleton numbered/versioned
    files retain their exact names.
    """
    partitioned_path = _partitioned_dataset_path(path)
    if partitioned_path is not None:
        path = partitioned_path
    else:
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


class ExportFiles:
    """Index export files once and reuse the index for all dataset lookups."""

    def __init__(self, root: Path) -> None:
        self._files = sorted(path for path in root.rglob("*") if path.is_file())

    def named(self, fragment: str, suffix: str = ".csv") -> list[Path]:
        needle = fragment.casefold()
        return [
            path
            for path in self._files
            if path.suffix == suffix and needle in path.name.casefold()
        ]


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

    catalog = BookCatalog()
    files = ExportFiles(root)
    recognized = False

    # Digital ownership is the broadest source for Kindle books and samples.
    ownership_files = files.named("Digital.Content.Ownership", ".json")
    if ownership_files:
        recognized = True
    for path in ownership_files:
        try:
            data = json.loads(path.read_text(encoding="utf-8-sig"))
            resource = data.get("resource", {})
        except (OSError, UnicodeError, json.JSONDecodeError, AttributeError) as exc:
            raise ExportError(f"cannot read {path}: {exc}") from exc
        rights = [right for right in data.get("rights", []) if isinstance(right, dict)]
        origins = {
            clean(right.get("origin", {}).get("originType")).casefold()
            for right in rights
            if isinstance(right.get("origin"), dict)
        }
        is_sample_resource = (
            clean(resource.get("resourceType")).casefold() == "kindleebooksample"
            or "sample" in origins
        )
        book, _ = catalog.get_or_create(
            resource.get("ASIN"),
            content_kind=_SAMPLE_KIND if is_sample_resource else _EBOOK_KIND,
        )
        if book:
            source_path = normalize_sharded_path(root, path)
            book.source = "kindle"
            book.set_title(resource.get("Product Name"), 30)
            book.add_raw(
                source_path,
                {f"resource.{name}": value for name, value in resource.items()},
            )
            for right in rights:
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
            book.is_default_content = book.is_default_content or bool(
                origins & _DEFAULT_ORIGIN_TYPES
            )

    # Unified Library Index identifies actual library items. Exclude rows which
    # only describe wish-list/not-interested/customer-metadata activity.
    relationship_paths = files.named("CustomerRelationshipIndex")
    if relationship_paths:
        recognized = True
    relationship_rows = list(_csv_rows(root, relationship_paths))
    for source_path, row in relationship_rows:
        if clean(row.get("Resource Type")).casefold() != "item":
            continue
        ownership_type = clean(row.get("Ownership Type")).casefold()
        if ownership_type not in _OWNER_TYPES:
            continue
        asin = clean(row.get("ASIN"))
        content_kind = (
            _SAMPLE_KIND if ownership_type == "sample owner" else _EBOOK_KIND
        )
        book, created = catalog.get_or_create(asin, content_kind=content_kind)
        if book:
            # ULI includes physical Amazon purchases. Kindle ownership evidence
            # takes precedence; an item found only in ULI is a print book.
            if created:
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
            book.set_series(row.get("Series Title"), row.get("Position In Collection"), 10)

    # BookRelation contains intrinsic item-to-series catalog relations. Its
    # acquisition timestamp is deliberately ignored.
    book_relation_paths = files.named("BookRelation.csv")
    if book_relation_paths:
        recognized = True
    for source_path, row in _csv_rows(root, book_relation_paths):
        if not clean(row.get("Product Name")):
            continue
        book, _ = catalog.get_or_create(row.get("ASIN"))
        if book:
            for variant in catalog.variants(row.get("ASIN")):
                variant.source = "kindle"
                variant.add_raw(source_path, row, ("ASIN", "Product Name"))
                variant.set_title(row.get("Product Name"), 35)

    # Personal documents have no ASIN, so preserve their Amazon document ID.
    document_paths = files.named("DocumentMetadata")
    if document_paths:
        recognized = True
    for source_path, row in _csv_rows(root, document_paths):
        book, _ = catalog.get_or_create(document_id=row.get("DocumentId"))
        if book:
            book.source = "kindle"
            book.is_default_content = is_default_personal_document(row)
            book.add_raw(
                source_path,
                row,
                tuple(name for name in row if name != "HasBeenDeleted"),
            )
            book.set_title(row.get("Title"), 50)

    # The Saga table provides explicit item-to-series metadata.
    saga_paths = files.named("CollectionRightsDatastore")
    if saga_paths:
        recognized = True
    for source_path, row in _csv_rows(root, saga_paths):
        if clean(row.get("record-type")).casefold() != "item":
            continue
        item_asin = clean_item_asin(row.get("item-ASIN"))
        # Some rows connect nested series and have no item title; they are not books.
        if not clean(row.get("item-product-name")) and not catalog.variants(item_asin):
            continue
        book, _ = catalog.get_or_create(item_asin)
        if book:
            for variant in catalog.variants(item_asin):
                variant.source = "kindle"
                variant.add_raw(
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
                variant.set_title(row.get("item-product-name"), 45)
                variant.set_series(
                    row.get("series-product-name"),
                    row.get("item-position-in-series"),
                    50,
                )

    # Enrich established books from metadata-only ULI relations. These files do
    # not seed records, preventing recommendations and wish-list items leaking in.
    for source_path, row in _csv_rows(
        root, files.named("CustomerAuthorNameRelationship")
    ):
        author = clean(row.get("Author Name"))
        for book in catalog.variants(row.get("ASIN")):
            if author:
                book.authors.add(author)
                book.add_raw(source_path, row)
    for source_path, row in _csv_rows(
        root, files.named("CustomerAuthorIdRelationship")
    ):
        for book in catalog.variants(row.get("ASIN")):
            book.add_raw(source_path, row)
    for source_path, row in _csv_rows(root, files.named("CustomerGenres")):
        genre = clean(row.get("Genre"))
        for book in catalog.variants(row.get("ASIN")):
            if genre:
                book.genres.add(genre)
                book.add_raw(source_path, row)
    for source_path, row in _csv_rows(
        root, files.named("CustomerRelationshipTypes")
    ):
        ownership_type = clean(row.get("Ownership Type")).casefold()
        content_kind = (
            _SAMPLE_KIND if ownership_type == "sample owner" else _EBOOK_KIND
        )
        book = catalog.variant(row.get("ASIN"), content_kind)
        if book and ownership_type in _OWNER_TYPES:
            book.add_raw(source_path, row)
    book_tag_groups = {"author", "catalog", "genre", "media-concept-node"}
    for source_path, row in _csv_rows(root, files.named("CustomerTags")):
        if clean(row.get("Tag Source Group")).casefold() not in book_tag_groups:
            continue
        for book in catalog.variants(row.get("ASIN")):
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

    result = catalog.values()
    if not show_default:
        result = (book for book in result if not book.is_default_content)
    if not show_samples:
        result = (book for book in result if not book.is_sample)
    if source != "all":
        result = (book for book in result if book.source == source)
    return sorted(result, key=lambda book: (book.title.casefold(), book.key))
