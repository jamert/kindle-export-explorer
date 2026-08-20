"""Reconstruct book metadata from an Amazon Kindle data export."""

from __future__ import annotations

import csv
import json
import re
import sys
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Iterator


_MISSING = {"", "not available", "not applicable", "null", "none"}
_DEFAULT_ORIGIN_TYPES = {"kindledictionary", "kindleuserguide"}
_OWNER_TYPES = {"item owner", "sample owner"}
_SAMPLE_KIND = "sample"
_EBOOK_KIND = "ebook"


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


@dataclass(frozen=True)
class CanonicalKey:
    asin: str | None = None
    document_id: str | None = None

    def __str__(self) -> str:
        if self.document_id is not None:
            return f"document:{self.document_id}"
        return f"asin:{self.asin or ''}"


@dataclass(frozen=True)
class Authors:
    """Ordered author names and unpaired Amazon author-page ASINs."""

    names: list[str]
    asins: list[str]


class DigitalOwnership(StrEnum):
    UNKNOWN = "unknown"
    DEFAULT = "default"
    KINDLE_SAMPLE = "kindle_sample"
    KINDLE_EBOOK = "kindle_ebook"
    PERSONAL_DOCUMENT = "personal_document"


@dataclass(frozen=True)
class Series:
    title: str
    asin: str | None
    position: str | None


@dataclass(frozen=True)
class BookCanonical:
    key: CanonicalKey
    title: str
    authors: Authors
    ownership_digital: DigitalOwnership
    ownership_print: bool
    series: Series | None
    genres: list[str]
    marketplace: str

    @property
    def asin(self) -> str:
        return self.key.asin or ""

    @property
    def document_id(self) -> str:
        return self.key.document_id or ""

    @property
    def link(self) -> str | None:
        if self.marketplace and self.key.asin:
            return f"https://{self.marketplace}/dp/{self.key.asin}"
        return None

    def as_dict(self, *, extra: bool = False) -> dict[str, object]:
        result: dict[str, object] = {
            "key": str(self.key),
            "asin": self.key.asin,
            "document_id": self.key.document_id,
            "title": self.title,
            "author": {"names": self.authors.names, "asins": self.authors.asins},
            "ownership_digital": self.ownership_digital.value,
            "ownership_print": self.ownership_print,
        }
        if extra:
            result.update(
                {
                    "series": (
                        {
                            "title": self.series.title,
                            "asin": self.series.asin,
                            "position": self.series.position,
                        }
                        if self.series is not None
                        else None
                    ),
                    "genres": self.genres,
                    "link": self.link,
                }
            )
        return result

    def as_row(self, *, extra: bool = False) -> list[str]:
        row = [
            str(self.key),
            self.asin,
            self.document_id,
            self.title,
            _joined(self.authors.names),
            self.ownership_digital.value,
            "true" if self.ownership_print else "false",
        ]
        if extra:
            row.extend(
                (
                    self.series.title if self.series else "",
                    (self.series.asin or "") if self.series else "",
                    (self.series.position or "") if self.series else "",
                    _joined(self.genres),
                    self.link or "",
                )
            )
        return row


@dataclass
class BookMetadata:
    """Metadata shared by ASIN-based Kindle and print source records.

    Availability depends on the export relations connected to an ASIN. In the
    observed export, every field is populated for at least one print record except
    ``series_asin``: that value currently comes from Kindle Saga metadata and is
    empty for all print records. It remains here because series identity belongs to
    the shared canonical book schema rather than to ownership.
    """

    title: str
    authors: list[str] = field(default_factory=list)
    author_asins: list[str] = field(default_factory=list)
    genres: set[str] = field(default_factory=set)
    series_title: str | None = None
    series_position: str | None = None
    series_asin: str | None = None
    sortable_title: str | None = None
    sortable_author: str | None = None
    marketplaces: set[str] = field(default_factory=set)
    _title_rank: int = field(default=-1, repr=False)
    _series_rank: int = field(default=-1, repr=False)

    def set_title(self, value: object, rank: int) -> None:
        title = clean(value)
        if title and rank >= self._title_rank:
            self.title = title
            self._title_rank = rank

    def set_series(
        self,
        title: object,
        position: object,
        rank: int,
        asin: object = "",
    ) -> None:
        series_title = clean(title)
        if series_title and rank >= self._series_rank:
            self.series_title = series_title
            self.series_position = clean(position) or None
            self.series_asin = clean(asin) or None
            self._series_rank = rank


@dataclass
class KindleBookRecord:
    asin: str
    metadata: BookMetadata
    sample: bool = False
    default: bool = False

    @property
    def key(self) -> str:
        kind = _SAMPLE_KIND if self.sample else _EBOOK_KIND
        return f"asin:{kind}:{self.asin}"


@dataclass
class PrintBookRecord:
    asin: str
    metadata: BookMetadata

    @property
    def key(self) -> str:
        return f"asin:{self.asin}"


@dataclass
class DocumentRecord:
    document_id: str
    title: str = ""
    provider: str = ""
    default: bool = False

    @property
    def key(self) -> str:
        return f"document:{self.document_id}"


AsinBookRecord = KindleBookRecord | PrintBookRecord


HEADERS = [
    "key",
    "asin",
    "document_id",
    "title",
    "author",
    "ownership_digital",
    "ownership_print",
]
EXTRA_HEADERS = [
    "series_title",
    "series_asin",
    "series_position",
    "genres",
    "link",
]


def _marketplace_domain(value: str) -> str:
    without_scheme = (
        value.casefold().removeprefix("https://").removeprefix("http://")
    )
    return without_scheme.split("/", 1)[0]


def _select_marketplace(key: str, values: set[str]) -> str:
    if not values:
        return ""
    ordered = sorted(
        values,
        key=lambda value: (_marketplace_domain(value), value.casefold()),
    )
    preferred = next(
        (value for value in ordered if _marketplace_domain(value).endswith("amazon.com")),
        ordered[0],
    )
    if len(values) > 1:
        alternatives = ", ".join(ordered)
        print(
            f"warning: {key} has multiple marketplaces ({alternatives}); using {preferred}",
            file=sys.stderr,
        )
    return preferred


class CanonicalizationService:
    """Convert ownership-specific source records into canonical books."""

    @staticmethod
    def _convert_metadata(
        metadata: BookMetadata,
        key: CanonicalKey,
        digital: DigitalOwnership,
        print_owned: bool,
    ) -> BookCanonical:
        names = list(metadata.authors)
        if not names and metadata.sortable_author:
            names.append(metadata.sortable_author)
        series = (
            Series(
                title=metadata.series_title,
                asin=metadata.series_asin,
                position=metadata.series_position,
            )
            if metadata.series_title is not None
            else None
        )
        return BookCanonical(
            key=key,
            title=metadata.sortable_title or metadata.title,
            authors=Authors(
                names=names,
                asins=list(metadata.author_asins),
            ),
            ownership_digital=digital,
            ownership_print=print_owned,
            series=series,
            genres=sorted(metadata.genres, key=str.casefold),
            marketplace=_select_marketplace(str(key), metadata.marketplaces),
        )

    @staticmethod
    def _merge_kindle_metadata(*records: KindleBookRecord) -> BookMetadata:
        preferred = sorted(records, key=lambda record: record.sample)
        merged = BookMetadata(
            title=next(
                (record.metadata.title for record in preferred if record.metadata.title),
                "",
            )
        )
        for record in preferred:
            metadata = record.metadata
            if merged.sortable_title is None and metadata.sortable_title is not None:
                merged.sortable_title = metadata.sortable_title
            if merged.sortable_author is None and metadata.sortable_author is not None:
                merged.sortable_author = metadata.sortable_author
            if merged.series_title is None and metadata.series_title is not None:
                merged.series_title = metadata.series_title
                merged.series_asin = metadata.series_asin
                merged.series_position = metadata.series_position
            for name in metadata.authors:
                if name not in merged.authors:
                    merged.authors.append(name)
            for asin in metadata.author_asins:
                if asin not in merged.author_asins:
                    merged.author_asins.append(asin)
            merged.genres.update(metadata.genres)
            merged.marketplaces.update(metadata.marketplaces)
        return merged

    @staticmethod
    def convert_kindle(*records: KindleBookRecord) -> list[BookCanonical]:
        if not records:
            return []
        asin = records[0].asin
        full_record = next((record for record in records if not record.sample), None)
        if full_record is not None:
            ownership = (
                DigitalOwnership.DEFAULT
                if full_record.default
                else DigitalOwnership.KINDLE_EBOOK
            )
        else:
            ownership = (
                DigitalOwnership.DEFAULT
                if any(record.default for record in records)
                else DigitalOwnership.KINDLE_SAMPLE
            )
        return [
            CanonicalizationService._convert_metadata(
                CanonicalizationService._merge_kindle_metadata(*records),
                CanonicalKey(asin=asin),
                ownership,
                False,
            )
        ]

    @staticmethod
    def convert_print(record: PrintBookRecord) -> BookCanonical:
        return CanonicalizationService._convert_metadata(
            record.metadata,
            CanonicalKey(asin=record.asin),
            DigitalOwnership.UNKNOWN,
            True,
        )

    @staticmethod
    def convert_document(record: DocumentRecord) -> BookCanonical:
        ownership = (
            DigitalOwnership.DEFAULT
            if record.default
            else DigitalOwnership.PERSONAL_DOCUMENT
        )
        names = [record.provider] if record.provider else []
        return BookCanonical(
            key=CanonicalKey(document_id=record.document_id),
            title=record.title,
            authors=Authors(names=names, asins=[]),
            ownership_digital=ownership,
            ownership_print=False,
            series=None,
            genres=[],
            marketplace="",
        )


class SourceRecordCatalog:
    """Keep ownership-specific records separate and deduplicate by source key."""

    def __init__(self) -> None:
        self._kindle: dict[tuple[str, bool], KindleBookRecord] = {}
        self._print: dict[str, PrintBookRecord] = {}
        self._documents: dict[str, DocumentRecord] = {}

    def kindle(
        self,
        asin: object,
        *,
        title: object = "",
        sample: bool = False,
        create: bool = True,
    ) -> KindleBookRecord | None:
        asin_value = _clean_identifier(asin)
        if not asin_value:
            return None
        key = (asin_value, sample)
        record = self._kindle.get(key)
        if record is None and create:
            record = KindleBookRecord(
                asin=asin_value,
                metadata=BookMetadata(title=clean(title)),
                sample=sample,
            )
            self._kindle[key] = record
        return record

    def printed(
        self,
        asin: object,
        *,
        title: object = "",
        create: bool = True,
    ) -> PrintBookRecord | None:
        asin_value = _clean_identifier(asin)
        if not asin_value:
            return None
        record = self._print.get(asin_value)
        if record is None and create:
            record = PrintBookRecord(
                asin=asin_value,
                metadata=BookMetadata(title=clean(title)),
            )
            self._print[asin_value] = record
        return record

    def document(self, document_id: object) -> DocumentRecord | None:
        document_value = _clean_identifier(document_id)
        if not document_value:
            return None
        record = self._documents.get(document_value)
        if record is None:
            record = DocumentRecord(document_id=document_value)
            self._documents[document_value] = record
        return record

    def records_for_asin(self, asin: object) -> list[AsinBookRecord]:
        asin_value = clean(asin)
        records: list[AsinBookRecord] = [
            record
            for (record_asin, _), record in self._kindle.items()
            if record_asin == asin_value
        ]
        printed = self._print.get(asin_value)
        if printed is not None:
            records.append(printed)
        return records

    def kindle_groups(self) -> Iterable[tuple[KindleBookRecord, ...]]:
        grouped: dict[str, list[KindleBookRecord]] = {}
        for record in self._kindle.values():
            grouped.setdefault(record.asin, []).append(record)
        return (tuple(records) for records in grouped.values())

    def print_records(self) -> Iterable[PrintBookRecord]:
        return self._print.values()

    def document_records(self) -> Iterable[DocumentRecord]:
        return self._documents.values()


def _clean_identifier(value: object) -> str:
    result = clean(value)
    return "" if result.casefold() == "invalid-asin" else result


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
) -> list[BookCanonical]:
    """Join source records and return canonical books found under *root*.

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

    catalog = SourceRecordCatalog()
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
        book = catalog.kindle(
            resource.get("ASIN"),
            title=resource.get("Product Name"),
            sample=is_sample_resource,
        )
        if book:
            book.metadata.set_title(resource.get("Product Name"), 30)
            book.default = book.default or bool(origins & _DEFAULT_ORIGIN_TYPES)

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
        if ownership_type == "sample owner":
            asin_record = catalog.kindle(
                asin,
                title=row.get("Product Name"),
                sample=True,
            )
        else:
            # ULI repeats Kindle purchases and also contains physical purchases.
            # Existing digital ownership takes precedence; otherwise this is the
            # best available evidence for a print record.
            asin_record = catalog.kindle(asin, create=False) or catalog.printed(
                asin,
                title=row.get("Product Name"),
            )
        if asin_record:
            metadata = asin_record.metadata
            metadata.set_title(row.get("Product Name"), 50)
            sortable_title = clean(row.get("Sortable Title"))
            if sortable_title:
                metadata.sortable_title = sortable_title
            sortable_author = clean(row.get("Sortable Author Name"))
            if sortable_author:
                metadata.sortable_author = sortable_author
            marketplace = clean(row.get("Marketplace"))
            if marketplace:
                metadata.marketplaces.add(marketplace)
            metadata.set_series(
                row.get("Series Title"),
                row.get("Position In Collection"),
                10,
            )

    # BookRelation contains intrinsic item-to-series catalog relations. Its
    # acquisition timestamp is deliberately ignored.
    book_relation_paths = files.named("BookRelation.csv")
    if book_relation_paths:
        recognized = True
    for _, row in _csv_rows(root, book_relation_paths):
        if not clean(row.get("Product Name")):
            continue
        for record in catalog.records_for_asin(row.get("ASIN")):
            record.metadata.set_title(row.get("Product Name"), 35)

    # Personal documents have no ASIN, so preserve their Amazon document ID.
    document_paths = files.named("DocumentMetadata")
    if document_paths:
        recognized = True
    for _, row in _csv_rows(root, document_paths):
        document = catalog.document(row.get("DocumentId"))
        if document:
            document.default = is_default_personal_document(row)
            document.provider = clean(row.get("DocumentProvider"))
            document.title = clean(row.get("Title"))

    # The Saga table provides explicit item-to-series metadata.
    saga_paths = files.named("CollectionRightsDatastore")
    if saga_paths:
        recognized = True
    for _, row in _csv_rows(root, saga_paths):
        if clean(row.get("record-type")).casefold() != "item":
            continue
        item_asin = clean_item_asin(row.get("item-ASIN"))
        records = catalog.records_for_asin(item_asin)
        # Some rows connect nested series or describe unowned catalog items.
        if not records:
            continue
        for record in records:
            record.metadata.set_title(row.get("item-product-name"), 45)
            record.metadata.set_series(
                row.get("series-product-name"),
                row.get("item-position-in-series"),
                50,
                row.get("series-ASIN"),
            )

    # Enrich established ASIN records from metadata-only ULI relations. These
    # files do not seed records, preventing recommendations and wish-list items.
    for _, row in _csv_rows(root, files.named("CustomerAuthorNameRelationship")):
        author = clean(row.get("Author Name"))
        if author:
            for book in catalog.records_for_asin(row.get("ASIN")):
                if author not in book.metadata.authors:
                    book.metadata.authors.append(author)
    for _, row in _csv_rows(root, files.named("CustomerAuthorIdRelationship")):
        author_asin = clean(row.get("Author ID"))
        if author_asin:
            for book in catalog.records_for_asin(row.get("ASIN")):
                if author_asin not in book.metadata.author_asins:
                    book.metadata.author_asins.append(author_asin)
    for _, row in _csv_rows(root, files.named("CustomerGenres")):
        genre = clean(row.get("Genre"))
        if genre:
            for book in catalog.records_for_asin(row.get("ASIN")):
                book.metadata.genres.add(genre)

    if not recognized:
        raise ExportError(f"no recognized Kindle export files found in {root}")

    result: list[BookCanonical] = []
    if source in {"kindle", "all"}:
        for group in catalog.kindle_groups():
            full_record = next(
                (record for record in group if not record.sample),
                None,
            )
            if full_record is not None:
                if show_default or not full_record.default:
                    result.extend(CanonicalizationService.convert_kindle(*group))
            elif show_samples:
                visible = tuple(
                    record for record in group if show_default or not record.default
                )
                result.extend(CanonicalizationService.convert_kindle(*visible))
        result.extend(
            CanonicalizationService.convert_document(record)
            for record in catalog.document_records()
            if show_default or not record.default
        )
    if source in {"print", "all"}:
        result.extend(
            CanonicalizationService.convert_print(record)
            for record in catalog.print_records()
        )
    return sorted(result, key=lambda book: (book.title.casefold(), str(book.key)))
