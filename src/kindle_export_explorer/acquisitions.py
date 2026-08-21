"""Reconstruct acquisition events from an Amazon Kindle data export."""

from __future__ import annotations

import csv
import json
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from pathlib import Path
from typing import Iterator

from .books import CanonicalKey, ExportError, ExportFiles, clean


# Canonical acquisition model

class AcquisitionEventType(StrEnum):
    SAMPLE_ACQUIRED = "sample_acquired"
    KINDLE_PURCHASED = "kindle_purchased"
    KINDLE_DEFAULT_ACQUIRED = "kindle_default_acquired"
    PRINT_ACQUIRED = "print_acquired"
    DOCUMENT_CREATED = "document_created"


@dataclass(frozen=True)
class AcquisitionEvent:
    type: AcquisitionEventType
    timestamp: datetime


@dataclass(frozen=True)
class BookAcquisition:
    key: CanonicalKey
    events: list[AcquisitionEvent]

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "events",
            sorted(
                self.events,
                key=lambda event: (event.timestamp, event.type.value),
            ),
        )

    @property
    def acquired_sample(self) -> datetime | None:
        return self._first_timestamp(AcquisitionEventType.SAMPLE_ACQUIRED)

    @property
    def acquired_book(self) -> datetime | None:
        return self._first_timestamp(
            AcquisitionEventType.KINDLE_PURCHASED,
            AcquisitionEventType.KINDLE_DEFAULT_ACQUIRED,
            AcquisitionEventType.PRINT_ACQUIRED,
            AcquisitionEventType.DOCUMENT_CREATED,
        )

    def _first_timestamp(
        self,
        *event_types: AcquisitionEventType,
    ) -> datetime | None:
        return next(
            (
                event.timestamp
                for event in self.events
                if event.type in event_types
            ),
            None,
        )


# Public reconstruction pipeline

def reconstruct_acquisitions(root: Path) -> list[BookAcquisition]:
    """Return acquisition timelines reconstructed from *root*."""
    root = root.expanduser()
    if not root.is_dir():
        raise ExportError(f"not a directory: {root}")

    catalog = _assemble_acquisition_records(root)
    result: list[BookAcquisition] = []
    for group in catalog.kindle_groups():
        result.append(AcquisitionCanonicalizationService.convert_kindle(*group))
    result.extend(
        AcquisitionCanonicalizationService.convert_print(record)
        for record in catalog.print_records()
    )
    result.extend(
        AcquisitionCanonicalizationService.convert_document(record)
        for record in catalog.document_records()
    )
    return sorted(result, key=lambda acquisition: str(acquisition.key))


# Canonical conversion

class AcquisitionCanonicalizationService:
    """Convert ownership-specific acquisition evidence into event timelines."""

    @staticmethod
    def convert_kindle(*records: KindleAcquisitionRecord) -> BookAcquisition:
        if not records:
            raise ValueError("at least one Kindle acquisition record is required")
        return BookAcquisition(
            key=CanonicalKey(asin=records[0].asin),
            events=AcquisitionCanonicalizationService._events(records),
        )

    @staticmethod
    def convert_print(record: PrintAcquisitionRecord) -> BookAcquisition:
        return BookAcquisition(
            key=CanonicalKey(asin=record.asin),
            events=AcquisitionCanonicalizationService._events((record,)),
        )

    @staticmethod
    def convert_document(record: DocumentAcquisitionRecord) -> BookAcquisition:
        return BookAcquisition(
            key=CanonicalKey(document_id=record.document_id),
            events=AcquisitionCanonicalizationService._events((record,)),
        )

    @staticmethod
    def _events(
        records: tuple[
            KindleAcquisitionRecord | PrintAcquisitionRecord | DocumentAcquisitionRecord,
            ...,
        ],
    ) -> list[AcquisitionEvent]:
        events = {
            AcquisitionEvent(record.event_type, record.timestamp)
            for record in records
            if record.timestamp is not None
        }
        return list(events)


# Ownership-specific source records

@dataclass(frozen=True)
class KindleAcquisitionRecord:
    asin: str
    event_type: AcquisitionEventType
    timestamp: datetime | None


@dataclass(frozen=True)
class PrintAcquisitionRecord:
    asin: str
    timestamp: datetime | None

    @property
    def event_type(self) -> AcquisitionEventType:
        return AcquisitionEventType.PRINT_ACQUIRED


@dataclass(frozen=True)
class DocumentAcquisitionRecord:
    document_id: str
    timestamp: datetime | None

    @property
    def event_type(self) -> AcquisitionEventType:
        return AcquisitionEventType.DOCUMENT_CREATED


# Source record collection

class AcquisitionSourceCatalog:
    """Deduplicate source acquisition evidence by identifier, event, and time."""

    def __init__(self) -> None:
        self._kindle: dict[str, list[KindleAcquisitionRecord]] = {}
        self._print: dict[str, PrintAcquisitionRecord] = {}
        self._documents: dict[str, DocumentAcquisitionRecord] = {}

    def add_kindle(self, record: KindleAcquisitionRecord) -> None:
        records = self._kindle.setdefault(record.asin, [])
        if record not in records:
            records.append(record)

    def add_print(self, record: PrintAcquisitionRecord) -> None:
        existing = self._print.get(record.asin)
        if existing is None or _earlier(record.timestamp, existing.timestamp):
            self._print[record.asin] = record

    def add_document(self, record: DocumentAcquisitionRecord) -> None:
        existing = self._documents.get(record.document_id)
        if existing is None or _earlier(record.timestamp, existing.timestamp):
            self._documents[record.document_id] = record

    def kindle_groups(self) -> Iterator[tuple[KindleAcquisitionRecord, ...]]:
        return (tuple(records) for records in self._kindle.values())

    def print_records(self) -> Iterator[PrintAcquisitionRecord]:
        return iter(self._print.values())

    def document_records(self) -> Iterator[DocumentAcquisitionRecord]:
        return iter(self._documents.values())


# Export parsing details

_ORIGIN_EVENT_TYPES = {
    "sample": AcquisitionEventType.SAMPLE_ACQUIRED,
    "purchase": AcquisitionEventType.KINDLE_PURCHASED,
    "kindledictionary": AcquisitionEventType.KINDLE_DEFAULT_ACQUIRED,
    "kindleuserguide": AcquisitionEventType.KINDLE_DEFAULT_ACQUIRED,
}


def _assemble_acquisition_records(root: Path) -> AcquisitionSourceCatalog:
    catalog = AcquisitionSourceCatalog()
    files = ExportFiles(root)
    recognized = False
    kindle_asins: set[str] = set()

    ownership_paths = files.named("Digital.Content.Ownership", ".json")
    if ownership_paths:
        recognized = True
    for path in ownership_paths:
        try:
            data = json.loads(path.read_text(encoding="utf-8-sig"))
            resource = data.get("resource", {})
        except (OSError, UnicodeError, json.JSONDecodeError, AttributeError) as exc:
            raise ExportError(f"cannot read {path}: {exc}") from exc
        asin = clean(resource.get("ASIN"))
        if not asin:
            continue
        kindle_asins.add(asin)
        for right in data.get("rights", []):
            if not isinstance(right, dict):
                continue
            if clean(right.get("rightType")).casefold() != "download":
                continue
            origin = right.get("origin", {})
            if not isinstance(origin, dict):
                continue
            event_type = _ORIGIN_EVENT_TYPES.get(
                clean(origin.get("originType")).casefold()
            )
            if event_type is None:
                continue
            catalog.add_kindle(
                KindleAcquisitionRecord(
                    asin=asin,
                    event_type=event_type,
                    timestamp=_parse_timestamp(right.get("acquiredDate")),
                )
            )

    relationship_paths = files.named("CustomerRelationshipIndex")
    if relationship_paths:
        recognized = True
    for row in _csv_rows(relationship_paths):
        if clean(row.get("Resource Type")).casefold() != "item":
            continue
        if clean(row.get("Ownership Type")).casefold() != "item owner":
            continue
        asin = clean(row.get("ASIN"))
        if not asin or asin in kindle_asins:
            continue
        catalog.add_print(
            PrintAcquisitionRecord(
                asin=asin,
                timestamp=_parse_timestamp(row.get("Relationship Creation Date")),
            )
        )

    document_paths = files.named("DocumentMetadata")
    if document_paths:
        recognized = True
    for row in _csv_rows(document_paths):
        document_id = clean(row.get("DocumentId"))
        if document_id:
            catalog.add_document(
                DocumentAcquisitionRecord(
                    document_id=document_id,
                    timestamp=_parse_timestamp(row.get("EntryCreationDate")),
                )
            )

    if not recognized:
        raise ExportError(f"no recognized Kindle export files found in {root}")
    return catalog


def _csv_rows(paths: list[Path]) -> Iterator[dict[str, str]]:
    for path in paths:
        try:
            with path.open(encoding="utf-8-sig", newline="") as stream:
                yield from csv.DictReader(stream)
        except (OSError, UnicodeError, csv.Error) as exc:
            raise ExportError(f"cannot read {path}: {exc}") from exc


def _parse_timestamp(value: object) -> datetime | None:
    timestamp = clean(value)
    if not timestamp:
        return None
    try:
        return datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
    except ValueError:
        return None


def _earlier(candidate: datetime | None, existing: datetime | None) -> bool:
    return candidate is not None and (existing is None or candidate < existing)
