"""Reconstruct book metadata from an Amazon Kindle data export."""

from __future__ import annotations

import csv
import json
import re
import sys
from collections.abc import Callable, Iterable, Iterator, Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path, PurePosixPath
from typing import Any, cast
from zipfile import BadZipFile
from zipfile import Path as ZipPath
from zipfile import ZipFile, is_zipfile

_MISSING = {"", "not available", "not applicable", "null", "none"}
_OWNER_TYPES = {"item owner", "sample owner"}
_SAMPLE_KIND = "sample"
_EBOOK_KIND = "ebook"


class ExportError(ValueError):
    """Raised when a path is not a recognizable Kindle export."""


type ExportPath = Path | ZipPath


# Canonical output model


@dataclass(frozen=True)
class CanonicalKey:
    asin: str | None = None
    document_id: str | None = None

    def __str__(self) -> str:
        if self.document_id is not None:
            return f"document:{self.document_id}"
        return f"asin:{self.asin or ''}"


type CanonicalKeyPredicate = Callable[[CanonicalKey], bool]


@dataclass(frozen=True)
class BooksSelector:
    """Own all user-requested filtering of reconstructed books."""

    show_default: bool = False
    show_samples: bool = False
    format: str = "kindle"  # TODO: introduce enum
    included: frozenset[str] = frozenset()
    excluded: frozenset[str] = frozenset()
    has_include: bool = (
        False  # TODO: looks awkward, should be combined with include/exclude
    )

    def __post_init__(self) -> None:
        if self.format not in {"kindle", "print", "all"}:
            raise ValueError(f"invalid format: {self.format}")

    @classmethod
    def from_cli(
        cls,
        *,
        show_default: bool,
        show_samples: bool,
        format: str,
        included: set[str] | None,
        excluded: set[str] | None,
    ) -> BooksSelector:
        """Build a selector, including the CLI's explicit-include override."""
        # if --include flag is provided, we mostly ignore other filter settings
        # TODO: move this logic to constructor instead of having it only in staticmethod
        explicit = included is not None
        return cls(
            show_default=show_default or explicit,
            show_samples=show_samples or explicit,
            format="all" if explicit else format,
            included=frozenset(included or ()),
            excluded=frozenset(excluded or ()),
            has_include=explicit,
        )

    def asin(self, value: str | None) -> bool:
        """
        Method to check if ASIN is not filtered; intentionally takes only value itself
        to avoid tying to specific record structure
        """
        identifier = value.casefold() if value else ""
        return (
            bool(identifier)
            and not (
                self.has_include
                and (
                    identifier not in self.included
                    and f"asin:{identifier}" not in self.included
                )
            )
            and (
                identifier not in self.excluded
                and f"asin:{identifier}" not in self.excluded
            )
        )

    def key(self, key: CanonicalKey) -> bool:
        identifiers = {
            value.casefold() for value in (str(key), key.asin, key.document_id) if value
        }
        return (
            not self.has_include or bool(identifiers & self.included)
        ) and not identifiers & self.excluded

    def source_dict(self, values: Mapping[str, object]) -> bool:
        """Select a parsed JSON object or CSV row by its book identifier."""
        asin = _clean_identifier(values.get("ASIN"))
        if not asin:
            asin = clean_item_asin(values.get("item-ASIN"))
        document_id = _clean_identifier(values.get("DocumentId"))
        if asin:
            return self.source_key(CanonicalKey(asin=asin))
        if document_id:
            return self.source_key(CanonicalKey(document_id=document_id))
        return False

    def source_key(self, key: CanonicalKey) -> bool:
        """Select a source record whose canonical key is already known."""
        return self.key(key)

    def collects_kindle(self) -> bool:
        return self.format in {"kindle", "all"}

    def collects_print(self) -> bool:
        return self.format in {"print", "all"}

    def collects_sample_only(self) -> bool:
        return self.show_samples

    def joined_kindle(
        self,
        records: tuple[KindleBookRecord, ...],
    ) -> tuple[KindleBookRecord, ...]:
        """Select a joined full-book/sample group before canonicalization."""
        full_record = next((record for record in records if not record.sample), None)
        if full_record is not None:
            return records if self.show_default or not full_record.default else ()
        if not self.show_samples:
            return ()
        return tuple(
            record for record in records if self.show_default or not record.default
        )

    def joined_document(self, record: DocumentRecord) -> bool:
        """Select a joined personal-document record before canonicalization."""
        return self.show_default or not record.default

    def canonical(self, book: BookCanonical) -> bool:
        if not self.key(book.key):
            return False
        if self.format == "kindle" and book.ownership_print:
            return False
        if self.format == "print" and not book.ownership_print:
            return False
        if not self.show_default and book.ownership_digital is DigitalOwnership.DEFAULT:
            return False
        if (  # noqa: SIM103
            not self.show_samples
            and book.ownership_digital is DigitalOwnership.KINDLE_SAMPLE
        ):
            return False
        return True


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
    position: int | None


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

    @property
    def series_link(self) -> str | None:
        if self.marketplace and self.series and self.series.asin:
            return f"https://{self.marketplace}/dp/{self.series.asin}"
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
                    "series_link": self.series_link,
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
                    (
                        str(self.series.position)
                        if self.series and self.series.position is not None
                        else ""
                    ),
                    _joined(self.genres),
                    self.link or "",
                    self.series_link or "",
                )
            )
        return row


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
    "series_link",
]


# Public reconstruction pipeline


def reconstruct_books(
    root: Path,
    *,
    selector: BooksSelector,
) -> list[BookCanonical]:
    """Join source records and return canonical books found under *root*.

    The selector is applied both to parsed source dictionaries and to canonical
    books. Activity fields (reading dates, progress, sessions, annotations, and
    account state) are deliberately not copied.
    """
    root = root.expanduser()
    catalog = _assemble_source_records(root, selector=selector)
    return [book for book in _canonicalize_records(catalog) if selector.canonical(book)]


def _assemble_source_records(
    root: Path,
    *,
    selector: BooksSelector,
) -> SourceRecordCatalog:
    files = ExportFiles(root)
    _raise_if_book_files_missing(files)

    catalog = SourceRecordCatalog(selector)

    # Digital ownership is the broadest source for Kindle books and samples.
    _process_digital_ownership_records(files, selector, catalog)

    # Category filtering can now remove most source records before any of the
    # metadata-only relations are read. Known Kindle keys remain available so a
    # hidden digital item is not later mistaken for a print purchase.
    catalog.retain_visible_kindle()

    # Unified Library Index identifies actual library items. Exclude rows which
    # only describe wish-list/not-interested/customer-metadata activity.
    _process_customer_relationship_index_records(files, selector, catalog)

    # BookRelation contains intrinsic item-to-series catalog relations. Its
    # acquisition timestamp is deliberately ignored.
    _process_book_relation_records(files, catalog)

    # Personal documents have no ASIN, so preserve their Amazon document ID.
    _process_document_metadata_records(files, selector, catalog)
    catalog.retain_visible_documents()

    # The Saga table provides explicit item-to-series metadata.
    _process_collection_rights_datastore_records(files, catalog)

    # Enrich established ASIN records from metadata-only ULI relations. These
    # files do not seed records, preventing recommendations and wish-list items.
    _process_customer_author_name_relationship_records(files, catalog)
    _process_customer_author_id_relationship_records(files, catalog)
    _process_customer_genres_records(files, catalog)

    return catalog


def _raise_if_book_files_missing(files: ExportFiles) -> None:
    """Raise when the export contains no files used to reconstruct books."""
    required_files = (
        files.named("Digital.Content.Ownership", ".json"),
        files.named("CustomerRelationshipIndex"),
        files.named("BookRelation.csv"),
        files.named("DocumentMetadata"),
        files.named("CollectionRightsDatastore"),
    )
    if not any(required_files):
        raise ExportError(f"no recognized Kindle export files found in {files.source}")


@dataclass(frozen=True, slots=True)
class DigitalContentOwnershipSourceRecord:
    resource_asin: str
    resource_type: str
    resource_product_name: str
    rights_acquired_date: str  # TODO: timestamp
    rights_origin_type: str
    content_download_start_date: str | None  # TODO: timestamp

    def is_sample(self) -> bool:
        return (
            self.resource_type == "KindleEBookSample"
            or self.rights_origin_type == "Sample"
        )

    def is_default(self) -> bool:
        return self.rights_origin_type in ("KindleDictionary", "KindleUserGuide")


def _process_digital_ownership_records(
    files: ExportFiles,
    selector: BooksSelector,
    catalog: SourceRecordCatalog,
) -> None:
    """
    Relevant fields:
    `resource.ASIN`
    `resource.resourceType` -> KindleEBook", "KindleEBookSample", "KindlePDoc" (personal document)
    `resource.Product Name` -> book name, repeated in other files
    `rights[].rightType` -> we only need "Download", because it has more information than corresponding "Lending"
      in my data, there was no case where I had Lending but didn't have Download
    `rights[].acquiredDate`
    `rights[].origin.originType` -> allows to separate purchases from default kindle items (and sample)
    `rights[].consumptions[].resourceItemType -> filter on "Content"; the records is optional
    `rights[].consumptions[].startDate` -> possible date when buyer engaged with the content (downloaded)
    """
    ownership_files = files.named("Digital.Content.Ownership", suffix=".json")
    for path in ownership_files:
        try:
            source_dict = cast(
                dict[str, object], json.loads(path.read_text(encoding="utf-8-sig"))
            )
        except (OSError, UnicodeError, json.JSONDecodeError, AttributeError) as exc:
            raise ExportError(f"cannot read {path}: {exc}") from exc

        resource = cast(dict[str, str], source_dict.get("resource", {}))
        if not selector.asin(
            resource.get("ASIN")
        ):  # NOTE: may filter Personal Documents
            continue

        # NOTE: data-based assumption that record always have at least one 'Download' right
        download_rights = [
            right
            for right in cast(
                list[dict[str, object]], source_dict.get("rights", [])
            )  # always non-empty in practice
            if right.get("rightType") == "Download"
        ]
        if not download_rights:
            continue  # no useful data then  TODO: register in filter
        download_right = download_rights[0]

        content_download_start_date: str | None = None
        if content_consumptions := [
            consumption
            for consumption in cast(
                list[dict[str, str]], download_right.get("consumptions", {})
            )
            if consumption["resourceItemType"] == "Content"
        ]:
            content_download_start_date = content_consumptions[0]["startDate"]

        record = DigitalContentOwnershipSourceRecord(
            resource_asin=resource["ASIN"],
            resource_type=resource["resourceType"],
            resource_product_name=resource.get(
                "Product Name", ""
            ),  # TODO: ignore records without valid product name
            rights_acquired_date=cast(str, download_right["acquiredDate"]),
            rights_origin_type=cast(dict[str, str], download_right["origin"]).get(
                "originType", ""
            ),  # TODO: needs to be cleaned up
            content_download_start_date=content_download_start_date,
        )

        catalog.put_digital_content_ownership_record(record)


@dataclass(frozen=True, slots=True)
class CustomerRelationshipIndexSourceRecord:
    asin: str
    product_name: str
    resource_type: str
    ownership_type: str
    sortable_title: str
    sortable_author_name: str
    marketplace: str
    series_title: str
    position_in_collection: str

    def is_owned_item(self) -> bool:
        return self.resource_type == "ITEM" and self.ownership_type in {
            "Item Owner",
            "Sample Owner",
        }

    def is_sample(self) -> bool:
        return self.ownership_type == "Sample Owner"


def _process_customer_relationship_index_records(
    files: ExportFiles,
    selector: BooksSelector,
    catalog: SourceRecordCatalog,
) -> None:
    for _, row in _csv_rows(
        files.root,
        files.named("CustomerRelationshipIndex"),
    ):
        record = CustomerRelationshipIndexSourceRecord(
            asin=clean(row.get("ASIN")),
            product_name=clean(row.get("Product Name")),
            resource_type=clean(row.get("Resource Type")),
            ownership_type=clean(row.get("Ownership Type")),
            sortable_title=clean(row.get("Sortable Title")),
            sortable_author_name=clean(row.get("Sortable Author Name")),
            marketplace=clean(row.get("Marketplace")),
            series_title=clean(row.get("Series Title")),
            position_in_collection=clean(row.get("Position In Collection")),
        )

        if not selector.asin(record.asin) or not record.is_owned_item():
            continue

        catalog.put_customer_relationship_index_record(record)


def _process_book_relation_records(
    files: ExportFiles,
    catalog: SourceRecordCatalog,
) -> None:
    for _, row in _csv_rows(
        files.root,
        files.named("BookRelation.csv"),
        lambda row: catalog.has_records_for_asin(row.get("ASIN")),
    ):
        if not clean(row.get("Product Name")):
            continue
        for record in catalog.records_for_asin(row.get("ASIN")):
            record.metadata.set_title(row.get("Product Name"), 35)


def _process_document_metadata_records(
    files: ExportFiles,
    selector: BooksSelector,
    catalog: SourceRecordCatalog,
) -> None:
    for _, row in _csv_rows(
        files.root,
        files.named("DocumentMetadata"),
        selector.source_dict,
    ):
        document = catalog.document(row.get("DocumentId"))
        if document:
            document.default = is_default_personal_document(row)
            document.provider = clean(row.get("DocumentProvider"))
            document.title = clean(row.get("Title"))


def _process_collection_rights_datastore_records(
    files: ExportFiles,
    catalog: SourceRecordCatalog,
) -> None:
    for _, row in _csv_rows(
        files.root,
        files.named("CollectionRightsDatastore"),
        lambda row: catalog.has_records_for_asin(clean_item_asin(row.get("item-ASIN"))),
    ):
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


def _process_customer_author_name_relationship_records(
    files: ExportFiles,
    catalog: SourceRecordCatalog,
) -> None:
    for _, row in _csv_rows(
        files.root,
        files.named("CustomerAuthorNameRelationship"),
        lambda row: catalog.has_records_for_asin(row.get("ASIN")),
    ):
        author = clean(row.get("Author Name"))
        if author:
            for book in catalog.records_for_asin(row.get("ASIN")):
                if author not in book.metadata.authors:
                    book.metadata.authors.append(author)


def _process_customer_author_id_relationship_records(
    files: ExportFiles,
    catalog: SourceRecordCatalog,
) -> None:
    for _, row in _csv_rows(
        files.root,
        files.named("CustomerAuthorIdRelationship"),
        lambda row: catalog.has_records_for_asin(row.get("ASIN")),
    ):
        author_asin = clean(row.get("Author ID"))
        if author_asin:
            for book in catalog.records_for_asin(row.get("ASIN")):
                if author_asin not in book.metadata.author_asins:
                    book.metadata.author_asins.append(author_asin)


def _process_customer_genres_records(
    files: ExportFiles,
    catalog: SourceRecordCatalog,
) -> None:
    for _, row in _csv_rows(
        files.root,
        files.named("CustomerGenres"),
        lambda row: catalog.has_records_for_asin(row.get("ASIN")),
    ):
        genre = clean(row.get("Genre"))
        if genre:
            for book in catalog.records_for_asin(row.get("ASIN")):
                book.metadata.genres.add(genre)


# Canonical conversion


class CanonicalizationService:
    """Convert ownership-specific source records into canonical books."""

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
                (
                    record.metadata.title
                    for record in preferred
                    if record.metadata.title
                ),
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


# Ownership-specific source records


@dataclass
class BookMetadata:
    """Metadata shared by ASIN-based Kindle and print source records.

    Availability depends on the export relations connected to an ASIN. Kindle Saga
    supplies series title and ASIN separately, while the Unified Library Index often
    encodes them together as ``<series title> <series ASIN>``. The ULI value is split
    while source records are assembled so Kindle and print records share the same
    metadata shape.
    """

    title: str
    authors: list[str] = field(default_factory=lambda: [])
    author_asins: list[str] = field(default_factory=lambda: [])
    genres: set[str] = field(default_factory=lambda: set())
    series_title: str | None = None
    series_position: int | None = None
    series_asin: str | None = None
    sortable_title: str | None = None
    sortable_author: str | None = None
    marketplaces: set[str] = field(default_factory=lambda: set())
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
            self.series_position = parse_series_position(position)
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


# Source record collection


class SourceRecordCatalog:
    """Keep selected ownership records separate and deduplicate by source key."""

    def __init__(self, selector: BooksSelector) -> None:
        self._selector = selector
        self._known_kindle: set[tuple[str, bool]] = set()
        self._kindle: dict[tuple[str, bool], KindleBookRecord] = {}
        self._print: dict[str, PrintBookRecord] = {}
        self._documents: dict[str, DocumentRecord] = {}

    def put_digital_content_ownership_record(
        self, record: DigitalContentOwnershipSourceRecord
    ) -> None:
        # TODO: store digital_content_ownership record
        if book := self.kindle(
            asin=record.resource_asin,
            title=record.resource_product_name,
            sample=record.is_sample(),
        ):
            book.metadata.set_title(record.resource_product_name, 30)
            book.default = book.default or record.is_default()

    def put_customer_relationship_index_record(
        self,
        record: CustomerRelationshipIndexSourceRecord,
    ) -> None:
        # NOTE: processing relies on the fact that digital content ownership records are already processed
        #   and kindle records already exists, so we have implicit coupling
        if record.is_sample():
            sample_record = self.kindle(record.asin, sample=True, create=False)
            if self.has_kindle(record.asin, sample=True) and sample_record is None:
                return
            full_record = self.kindle(record.asin, create=False)
            if self.has_kindle(record.asin, sample=False) and full_record is None:
                return
            if full_record is None and not self._selector.collects_sample_only():
                return
            asin_record = self.kindle(
                record.asin,
                title=record.product_name,
                sample=True,
            )
        else:
            # ULI repeats Kindle purchases and also contains physical purchases.
            # Existing digital ownership takes precedence; otherwise this is the
            # best available evidence for a print record.
            if self.has_kindle(record.asin, sample=False):
                asin_record = self.kindle(record.asin, create=False)
            else:
                asin_record = self.printed(
                    record.asin,
                    title=record.product_name,
                )
        if asin_record:
            metadata = asin_record.metadata
            metadata.set_title(record.product_name, 50)
            if record.sortable_title:
                metadata.sortable_title = record.sortable_title
            if record.sortable_author_name:
                metadata.sortable_author = record.sortable_author_name
            if record.marketplace:
                metadata.marketplaces.add(record.marketplace)
            series_title, series_asin = split_series_title_and_asin(record.series_title)
            metadata.set_series(
                series_title,
                record.position_in_collection,
                10,
                series_asin,
            )

    def kindle(
        self,
        asin: object,
        *,
        title: object = "",
        sample: bool = False,
        create: bool = True,
    ) -> KindleBookRecord | None:
        asin_value = _clean_identifier(asin)
        if not asin_value or not self._selector.source_key(
            CanonicalKey(asin=asin_value)
        ):
            return None
        key = (asin_value, sample)
        if create:
            self._known_kindle.add(key)
        if not self._selector.collects_kindle():
            return None
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
        if (
            not asin_value
            or not self._selector.collects_print()
            or not self._selector.source_key(CanonicalKey(asin=asin_value))
        ):
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
        if (
            not document_value
            or not self._selector.collects_kindle()
            or not self._selector.source_key(CanonicalKey(document_id=document_value))
        ):
            return None
        record = self._documents.get(document_value)
        if record is None:
            record = DocumentRecord(document_id=document_value)
            self._documents[document_value] = record
        return record

    def has_kindle(self, asin: object, *, sample: bool) -> bool:
        return (_clean_identifier(asin), sample) in self._known_kindle

    def retain_visible_kindle(self) -> None:
        """Discard groups which cannot produce output before reading join tables."""
        retained: dict[tuple[str, bool], KindleBookRecord] = {}
        for group in self.kindle_groups():
            retained.update(
                ((record.asin, record.sample), record)
                for record in self._selector.joined_kindle(group)
            )
        self._kindle = retained

    def retain_visible_documents(self) -> None:
        self._documents = {
            key: record
            for key, record in self._documents.items()
            if self._selector.joined_document(record)
        }

    def has_records_for_asin(self, asin: object) -> bool:
        return bool(self.records_for_asin(asin))

    def records_for_asin(self, asin: object) -> list[AsinBookRecord]:
        asin_value = _clean_identifier(asin)
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


# Parsing and export-format details


def clean(value: object) -> str:
    if value is None:
        return ""
    result = str(value).strip()
    return "" if result.casefold() in _MISSING else result


def parse_series_position(value: object) -> int | None:
    result = clean(value)
    if not result:
        return None
    try:
        return int(result)
    except ValueError:
        return None


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


def _marketplace_domain(value: str) -> str:
    without_scheme = value.casefold().removeprefix("https://").removeprefix("http://")
    return without_scheme.split("/", 1)[0]


def _select_marketplace(key: str, values: set[str]) -> str:
    if not values:
        return ""
    ordered = sorted(
        values,
        key=lambda value: (_marketplace_domain(value), value.casefold()),
    )
    preferred = next(
        (
            value
            for value in ordered
            if _marketplace_domain(value).endswith("amazon.com")
        ),
        ordered[0],
    )
    if len(values) > 1:
        alternatives = ", ".join(ordered)
        print(
            f"warning: {key} has multiple marketplaces ({alternatives}); using {preferred}",
            file=sys.stderr,
        )
    return preferred


def _clean_identifier(value: object) -> str:
    result = clean(value)
    return "" if result.casefold() == "invalid-asin" else result


_SERIES_TITLE_WITH_ASIN = re.compile(r"^(?P<title>.+?)\s+(?P<asin>B[0-9A-Z]{9})$")
_NUMBERED_SHARD = re.compile(r"^(?P<base>.+)\.(?P<number>\d+)(?P<extension>\.[^.]+)$")
_VERSIONED_DATASET = re.compile(r"^(?P<base>.+)\.\d+\.\d+$")


def split_series_title_and_asin(value: object) -> tuple[str, str | None]:
    """Split the ULI encoding ``<series title> <series ASIN>`` when present."""
    title = clean(value)
    match = _SERIES_TITLE_WITH_ASIN.fullmatch(title)
    if match is None:
        return title, None
    return match.group("title"), match.group("asin")


def _relative_path(root: ExportPath, path: ExportPath) -> str:
    """Return a POSIX relative path for pathlib and zipfile paths."""
    relative = cast(Any, path).relative_to(cast(Any, root))
    return relative if isinstance(relative, str) else relative.as_posix()


def _partitioned_dataset_path(root: ExportPath, path: ExportPath) -> str | None:
    """Return a wildcard path when sibling version directories form one dataset."""
    item = cast(Any, path)
    match = _VERSIONED_DATASET.match(item.stem)
    if not match or item.parent.name != item.stem:
        return None
    base = match.group("base")
    matching_files: list[ExportPath] = []
    for directory in item.parent.parent.iterdir():
        directory_match = _VERSIONED_DATASET.match(directory.name)
        if not directory.is_dir() or not directory_match:
            continue
        if directory_match.group("base") != base:
            continue
        candidate = directory / f"{directory.name}{item.suffix}"
        if candidate.is_file():
            matching_files.append(candidate)
    if len(matching_files) < 2:
        return None
    parent = _relative_path(root, item.parent.parent)
    return (PurePosixPath(parent) / f"{base}.*" / f"*{item.suffix}").as_posix()


def normalize_sharded_path(root: ExportPath, path: ExportPath) -> str:
    """Return a stable export-relative path for shards and dataset partitions.

    Numbered files in one directory collapse to ``shard.<extension>``. Files whose
    matching version appears in multiple sibling dataset directories collapse to a
    two-level wildcard such as ``Dataset.*/*.csv``. Singleton numbered/versioned
    files retain their exact names.
    """
    partitioned_path = _partitioned_dataset_path(root, path)
    if partitioned_path is not None:
        return partitioned_path

    item = cast(Any, path)
    match = _NUMBERED_SHARD.match(item.name)
    if match:
        pattern = f"{match.group('base')}.*{match.group('extension')}"
        matching_siblings = (
            sibling
            for sibling in item.parent.glob(pattern)
            if (sibling_match := _NUMBERED_SHARD.match(sibling.name))
            and sibling_match.group("base") == match.group("base")
            and sibling_match.group("extension") == match.group("extension")
        )
        if sum(1 for _ in matching_siblings) > 1:
            parent = _relative_path(root, item.parent)
            return (
                PurePosixPath(parent) / f"shard{match.group('extension')}"
            ).as_posix()
    return _relative_path(root, path)


def _csv_rows(
    root: ExportPath,
    paths: Iterable[ExportPath],
    predicate: Callable[[dict[str, str]], bool] | None = None,
) -> Iterator[tuple[str, dict[str, str]]]:
    for path in paths:
        try:
            with path.open(encoding="utf-8-sig", newline="") as stream:
                source_path = normalize_sharded_path(root, path)
                for row in csv.DictReader(stream):
                    if predicate is None or predicate(row):
                        yield source_path, row
        except (OSError, UnicodeError, csv.Error) as exc:
            raise ExportError(f"cannot read {path}: {exc}") from exc


class ExportFiles:
    """Index files in an unpacked export directory or ZIP archive."""

    def __init__(self, source: Path) -> None:
        self.source = source.expanduser()
        self._archive: ZipFile | None = None
        if self.source.is_dir():
            self.root: ExportPath = self.source
        elif self.source.is_file() and is_zipfile(self.source):
            try:
                self._archive = ZipFile(self.source)
                archive_root = ZipPath(self._archive)
                children = [
                    child
                    for child in archive_root.iterdir()
                    if child.name != "__MACOSX"
                ]
                # Accept archives made from either the contents of Kindle/ or the
                # Kindle/ directory itself.
                self.root = (
                    children[0]
                    if len(children) == 1
                    and children[0].is_dir()
                    and children[0].name.casefold() == "kindle"
                    else archive_root
                )
            except (OSError, BadZipFile) as exc:
                raise ExportError(
                    f"cannot read ZIP archive {self.source}: {exc}"
                ) from exc
        else:
            raise ExportError(f"not a directory or ZIP archive: {self.source}")

        self._files = sorted(
            (path for path in self.root.rglob("*") if path.is_file()),
            key=lambda path: _relative_path(self.root, path),
        )

    def __iter__(self) -> Iterator[ExportPath]:
        return iter(self._files)

    def relative(self, path: ExportPath) -> str:
        return _relative_path(self.root, path)

    def named(self, fragment: str, suffix: str = ".csv") -> list[ExportPath]:
        needle = fragment.casefold()
        return [
            path
            for path in self._files
            if path.suffix == suffix and needle in path.name.casefold()
        ]


def _canonicalize_records(catalog: SourceRecordCatalog) -> list[BookCanonical]:
    result: list[BookCanonical] = []
    for group in catalog.kindle_groups():
        result.extend(CanonicalizationService.convert_kindle(*group))
    result.extend(
        CanonicalizationService.convert_document(record)
        for record in catalog.document_records()
    )
    result.extend(
        CanonicalizationService.convert_print(record)
        for record in catalog.print_records()
    )
    return sorted(result, key=lambda book: (book.title.casefold(), str(book.key)))
