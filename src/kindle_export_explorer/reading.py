"""Collect book-related reading records from an Amazon Kindle data export."""

from __future__ import annotations

import csv
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Iterator

from .books import CanonicalKey, ExportError, ExportFiles, clean


# Per-book reading record collection

@dataclass(frozen=True)
class DeviceSessionsSummary:
    start: datetime | None
    end: datetime | None
    total_reading_millis: int
    total_reading_humanized: str
    total_page_flips: int
    total_count: int


@dataclass(frozen=True)
class WhispersyncRecordSummary:
    start: datetime | None
    end: datetime | None
    dates_unique: int


@dataclass
class BookReading:
    key: CanonicalKey
    device_sessions: list[DeviceReadingSessionRecord] = field(default_factory=list)
    insights_sessions: list[ReadingInsightsSessionRecord] = field(default_factory=list)
    whispersync_records: list[WhispersyncRecord] = field(default_factory=list)
    reading_action_containers: list[ReadingActionContainerRecord] = field(
        default_factory=list
    )
    auto_mark_as_read_records: list[AutoMarkAsReadRecord] = field(
        default_factory=list
    )
    completion_records: list[TitleCompletionRecord] = field(default_factory=list)

    @property
    def device_sessions_summary(self) -> DeviceSessionsSummary | None:
        if not self.device_sessions:
            return None
        starts = [session.start for session in self.device_sessions if session.start]
        ends = [session.end for session in self.device_sessions if session.end]
        reading_millis = [
            session.total_reading_millis
            for session in self.device_sessions
            if session.total_reading_millis
        ]
        page_flips = [
            session.number_of_page_flips
            for session in self.device_sessions
            if session.number_of_page_flips
        ]
        total_reading_millis = sum(reading_millis)
        return DeviceSessionsSummary(
            start=min(starts, default=None),
            end=max(ends, default=None),
            total_reading_millis=total_reading_millis,
            total_reading_humanized=_humanize_millis(total_reading_millis),
            total_page_flips=sum(page_flips),
            total_count=len(reading_millis),
        )

    @property
    def whispersync_record_summary(self) -> WhispersyncRecordSummary:
        records = [
            record
            for record in self.whispersync_records
            if record.annotation_type == "kindle.most_recent_read"
        ]
        creation_dates = [
            record.creation_date for record in records if record.creation_date
        ]
        modified_dates = [
            record.customer_modified_date
            for record in records
            if record.customer_modified_date
        ]
        start = min(creation_dates, default=None)
        unique_dates = {value.date() for value in modified_dates}
        if start is not None:
            unique_dates.add(start.date())
        return WhispersyncRecordSummary(
            start=start,
            end=max(modified_dates, default=None),
            dates_unique=len(unique_dates),
        )


# Public reconstruction pipeline

def reconstruct_reading(root: Path) -> list[BookReading]:
    """Collect reading records grouped by canonical book key."""
    root = root.expanduser()
    if not root.is_dir():
        raise ExportError(f"not a directory: {root}")

    catalog = _assemble_reading_records(root)
    return sorted(catalog.records(), key=lambda reading: str(reading.key))


# Source-specific reading records

@dataclass(frozen=True)
class DeviceReadingSessionRecord:
    asin: str
    start: datetime | None
    end: datetime | None
    content_type: str
    total_reading_millis: int | None
    number_of_page_flips: int | None
    device_family: str
    device_serial_number: str
    device_software_version: str
    preferred_marketplace: str
    purchased_marketplace: str


@dataclass(frozen=True)
class ReadingInsightsSessionRecord:
    asin: str
    start: datetime | None
    end: datetime | None
    total_reading_millis: int | None
    product_name: str
    reading_marketplace: str
    personal_document_id: str


@dataclass(frozen=True)
class WhispersyncRecord:
    asin: str
    non_asin: str
    annotation_type: str
    content_type: str
    creation_date: datetime | None
    customer_modified_date: datetime | None
    last_updated_date: datetime | None
    product_name: str
    device_serial_number: str
    third_party_device: str


@dataclass(frozen=True)
class ReadingActionContainerRecord:
    asin: str
    created_at: datetime | None
    display_start: datetime | None
    display_end: datetime | None
    reading_action_display: str
    entry_point: str
    book_format: str
    country: str
    device_family: str
    preferred_marketplace: str


@dataclass(frozen=True)
class AutoMarkAsReadRecord:
    asin: str
    created_at: datetime | None
    file_auto_marked_as_read: str
    content_type: str
    country: str
    device_family: str
    preferred_marketplace: str


@dataclass(frozen=True)
class TitleCompletionRecord:
    asin: str
    completed_on: date | None
    completion_type: str
    product_name: str
    personal_document_id: str


# Source record collection

class ReadingSourceCatalog:
    def __init__(self) -> None:
        self._records: dict[CanonicalKey, BookReading] = {}

    def add_device_session(self, record: DeviceReadingSessionRecord) -> None:
        self._for_asin(record.asin).device_sessions.append(record)

    def add_insights_session(self, record: ReadingInsightsSessionRecord) -> None:
        self._for_asin(record.asin).insights_sessions.append(record)

    def add_whispersync(
        self,
        key: CanonicalKey,
        record: WhispersyncRecord,
    ) -> None:
        self._for_key(key).whispersync_records.append(record)

    def add_reading_action_container(
        self,
        record: ReadingActionContainerRecord,
    ) -> None:
        self._for_asin(record.asin).reading_action_containers.append(record)

    def add_auto_mark_as_read(self, record: AutoMarkAsReadRecord) -> None:
        self._for_asin(record.asin).auto_mark_as_read_records.append(record)

    def add_completion(self, record: TitleCompletionRecord) -> None:
        self._for_asin(record.asin).completion_records.append(record)

    def records(self) -> Iterator[BookReading]:
        return iter(self._records.values())

    def _for_asin(self, asin: str) -> BookReading:
        return self._for_key(CanonicalKey(asin=asin))

    def _for_key(self, key: CanonicalKey) -> BookReading:
        return self._records.setdefault(key, BookReading(key=key))


# Aggregation details

def _humanize_millis(value: int) -> str:
    total_minutes = (value + 30_000) // 60_000
    hours, minutes = divmod(total_minutes, 60)
    return f"{hours}h{minutes}m" if hours else f"{minutes}m"


# Export parsing details

_READING_ANNOTATION_TYPES = {
    "kindle.last_read",
    "kindle.most_recent_read",
}


def _assemble_reading_records(root: Path) -> ReadingSourceCatalog:
    catalog = ReadingSourceCatalog()
    files = ExportFiles(root)
    recognized = False

    paths = files.named("Kindle.Devices.ReadingSession")
    recognized |= bool(paths)
    for row in _csv_rows(paths):
        asin = clean(row.get("ASIN"))
        content_type = clean(row.get("content_type"))
        if not asin or content_type not in {"E-Book", "E-Book Sample"}:
            continue
        catalog.add_device_session(
            DeviceReadingSessionRecord(
                asin=asin,
                start=_parse_timestamp(row.get("start_timestamp")),
                end=_parse_timestamp(row.get("end_timestamp")),
                content_type=content_type,
                total_reading_millis=_parse_int(row.get("total_reading_millis")),
                number_of_page_flips=_parse_int(row.get("number_of_page_flips")),
                device_family=clean(row.get("device_family")),
                device_serial_number=clean(row.get("device_serial_number")),
                device_software_version=clean(row.get("device_software_version")),
                preferred_marketplace=clean(row.get("preferred_marketplace")),
                purchased_marketplace=clean(row.get("purchased_marketplace")),
            )
        )

    paths = files.named("reading-insights-sessions_with_adjustments")
    recognized |= bool(paths)
    for row in _csv_rows(paths):
        asin = clean(row.get("ASIN"))
        if not asin:
            continue
        catalog.add_insights_session(
            ReadingInsightsSessionRecord(
                asin=asin,
                start=_parse_timestamp(row.get("start_time")),
                end=_parse_timestamp(row.get("end_time")),
                total_reading_millis=_parse_int(
                    row.get("total_reading_milliseconds")
                ),
                product_name=clean(row.get("product_name")),
                reading_marketplace=clean(row.get("reading_marketplace")),
                personal_document_id=clean(row.get("personal_document_id")),
            )
        )

    paths = files.named("whispersync")
    recognized |= bool(paths)
    for row in _csv_rows(paths):
        key = _whispersync_key(row)
        annotation_type = clean(row.get("Annotation Type"))
        if key is None or annotation_type not in _READING_ANNOTATION_TYPES:
            continue
        catalog.add_whispersync(
            key,
            WhispersyncRecord(
                asin=clean(row.get("ASIN")),
                non_asin=clean(row.get("Non ASIN")),
                annotation_type=annotation_type,
                content_type=clean(row.get("ContentType")),
                creation_date=_parse_timestamp(row.get("Creation Date")),
                customer_modified_date=_parse_timestamp(
                    row.get("Customer modified date on device")
                ),
                last_updated_date=_parse_timestamp(row.get("LastUpdatedDate")),
                product_name=clean(row.get("Product Name")),
                device_serial_number=clean(row.get("Device Serial Number")),
                third_party_device=clean(row.get("Third Party Device")),
            ),
        )

    paths = files.named("ReadingActionsContainers")
    recognized |= bool(paths)
    for row in _csv_rows(paths):
        asin = clean(row.get("ASIN"))
        if not asin:
            continue
        catalog.add_reading_action_container(
            ReadingActionContainerRecord(
                asin=asin,
                created_at=_parse_timestamp(row.get("created_timestamp")),
                display_start=_parse_timestamp(row.get("display_start")),
                display_end=_parse_timestamp(row.get("display_end")),
                reading_action_display=clean(row.get("reading_action_display")),
                entry_point=clean(row.get("entry_point")),
                book_format=clean(row.get("book_format")),
                country=clean(row.get("country")),
                device_family=clean(row.get("device_family")),
                preferred_marketplace=clean(row.get("preferred_marketplace")),
            )
        )

    paths = files.named("autoMarkAsRead")
    recognized |= bool(paths)
    for row in _csv_rows(paths):
        asin = clean(row.get("active_ASIN"))
        if not asin:
            continue
        catalog.add_auto_mark_as_read(
            AutoMarkAsReadRecord(
                asin=asin,
                created_at=_parse_timestamp(row.get("created_timestamp")),
                file_auto_marked_as_read=clean(
                    row.get("file_auto_marked_as_read")
                ),
                content_type=clean(row.get("content_type")),
                country=clean(row.get("country")),
                device_family=clean(row.get("device_family")),
                preferred_marketplace=clean(row.get("preferred_marketplace")),
            )
        )

    paths = files.named("UserUniqueTitlesCompleted")
    recognized |= bool(paths)
    for row in _csv_rows(paths):
        parsed = _parse_completion_key(row.get("asin_date_and_content_type"))
        if parsed is None:
            continue
        asin, completed_on, completion_type = parsed
        catalog.add_completion(
            TitleCompletionRecord(
                asin=asin,
                completed_on=completed_on,
                completion_type=completion_type,
                product_name=clean(row.get("product_name")),
                personal_document_id=clean(row.get("personal_document_id")),
            )
        )

    if not recognized:
        raise ExportError(f"no recognized Kindle reading files found in {root}")
    return catalog


def _csv_rows(paths: list[Path]) -> Iterator[dict[str, str]]:
    for path in paths:
        try:
            with path.open(encoding="utf-8-sig", newline="") as stream:
                yield from csv.DictReader(stream)
        except (OSError, UnicodeError, csv.Error) as exc:
            raise ExportError(f"cannot read {path}: {exc}") from exc


def _whispersync_key(row: dict[str, str]) -> CanonicalKey | None:
    asin = clean(row.get("ASIN"))
    if asin:
        return CanonicalKey(asin=asin)
    if clean(row.get("ContentType")).casefold() == "pdoc":
        document_id = clean(row.get("Non ASIN"))
        if document_id:
            return CanonicalKey(document_id=document_id)
    return None


def _parse_completion_key(value: object) -> tuple[str, date, str] | None:
    parts = clean(value).split("_", 2)
    if len(parts) != 3:
        return None
    asin, date_text, completion_type = parts
    if not asin or not completion_type:
        return None
    try:
        return asin, date.fromisoformat(date_text), completion_type
    except ValueError:
        return None


def _parse_timestamp(value: object) -> datetime | None:
    timestamp = clean(value)
    if not timestamp:
        return None
    try:
        return datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
    except ValueError:
        return None


def _parse_int(value: object) -> int | None:
    number = clean(value)
    if not number:
        return None
    try:
        return int(float(number))
    except ValueError:
        return None
