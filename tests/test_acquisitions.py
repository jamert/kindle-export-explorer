import csv
import json
from datetime import datetime, timezone
from pathlib import Path

from click.testing import CliRunner

from kindle_export_reassembly import (
    AcquisitionEvent,
    AcquisitionEventType,
    BookAcquisition,
    CanonicalKey,
    reconstruct_acquisitions,
)
from kindle_export_reassembly.cli import main


def write_csv(root: Path, relative: str, headers: list[str], rows: list[list[str]]) -> None:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.writer(stream)
        writer.writerow(headers)
        writer.writerows(rows)


def test_reconstructs_kindle_print_and_document_acquisition_timelines(
    tmp_path: Path,
) -> None:
    ownership = tmp_path / "Digital.Content.Ownership"
    ownership.mkdir()
    (ownership / "Digital.Content.Ownership.1.json").write_text(
        json.dumps(
            {
                "resource": {
                    "ASIN": "BOOK",
                    "resourceType": "KindleEBookSample",
                },
                "rights": [
                    {
                        "rightType": "Download",
                        "acquiredDate": "2024-01-01T10:00:00Z",
                        "origin": {"originType": "Sample"},
                        "consumptions": [
                            {
                                "resourceItemType": "Content",
                                "startDate": "2024-01-01T10:05:00Z",
                            }
                        ],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    (ownership / "Digital.Content.Ownership.2.json").write_text(
        json.dumps(
            {
                "resource": {"ASIN": "BOOK", "resourceType": "KindleEBook"},
                "rights": [
                    {
                        "rightType": "Download",
                        "acquiredDate": "2024-01-03T12:00:00Z",
                        "origin": {"originType": "Purchase"},
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    write_csv(
        tmp_path,
        "uli/CustomerRelationshipIndex.csv",
        [
            "ASIN",
            "Resource Type",
            "Ownership Type",
            "Relationship Creation Date",
        ],
        [
            ["BOOK", "ITEM", "Item Owner", "2024-01-03T12:00:01Z"],
            ["PRINT", "ITEM", "Item Owner", "2023-02-01T09:00:00Z"],
            ["PRINT", "ITEM", "Item Owner", "2023-02-02T09:00:00Z"],
        ],
    )
    write_csv(
        tmp_path,
        "Kindle.KindleDocs.DocumentMetadata.csv",
        ["DocumentId", "EntryCreationDate"],
        [["DOC", "2022-03-04T05:06:07Z"]],
    )

    acquisitions = reconstruct_acquisitions(tmp_path)
    by_key = {str(acquisition.key): acquisition for acquisition in acquisitions}

    assert set(by_key) == {"asin:BOOK", "asin:PRINT", "document:DOC"}
    assert [event.type for event in by_key["asin:BOOK"].events] == [
        AcquisitionEventType.SAMPLE_ACQUIRED,
        AcquisitionEventType.KINDLE_PURCHASED,
    ]
    assert [event.timestamp for event in by_key["asin:BOOK"].events] == [
        datetime(2024, 1, 1, 10, tzinfo=timezone.utc),
        datetime(2024, 1, 3, 12, tzinfo=timezone.utc),
    ]
    assert by_key["asin:BOOK"].acquired_sample == datetime(
        2024, 1, 1, 10, tzinfo=timezone.utc
    )
    assert by_key["asin:BOOK"].acquired_book == datetime(
        2024, 1, 3, 12, tzinfo=timezone.utc
    )
    assert by_key["asin:PRINT"].events[0].type == (
        AcquisitionEventType.PRINT_ACQUIRED
    )
    assert by_key["asin:PRINT"].events[0].timestamp == datetime(
        2023, 2, 1, 9, tzinfo=timezone.utc
    )
    assert by_key["asin:PRINT"].acquired_sample is None
    assert by_key["asin:PRINT"].acquired_book == datetime(
        2023, 2, 1, 9, tzinfo=timezone.utc
    )
    assert by_key["document:DOC"].events[0].type == (
        AcquisitionEventType.DOCUMENT_CREATED
    )
    assert all(
        event.timestamp != datetime(2024, 1, 1, 10, 5, tzinfo=timezone.utc)
        for event in by_key["asin:BOOK"].events
    )

    runner = CliRunner()
    tsv_result = runner.invoke(
        main,
        ["--acquisition", "--source", "all", str(tmp_path)],
    )
    assert tsv_result.exit_code == 0
    tsv_rows = {
        row["key"]: row
        for row in csv.DictReader(tsv_result.output.splitlines(), dialect="excel-tab")
    }
    assert tsv_rows["asin:BOOK"]["acquired_sample"] == (
        "2024-01-01T10:00:00Z"
    )
    assert tsv_rows["asin:BOOK"]["acquired_book"] == (
        "2024-01-03T12:00:00Z"
    )
    assert tsv_rows["asin:PRINT"]["acquired_sample"] == ""
    assert tsv_rows["asin:PRINT"]["acquired_book"] == (
        "2023-02-01T09:00:00Z"
    )

    json_result = runner.invoke(
        main,
        ["--acquisition", "--jsonl", "--source", "all", str(tmp_path)],
    )
    assert json_result.exit_code == 0
    json_rows = {
        row["key"]: row
        for row in map(json.loads, json_result.output.splitlines())
    }
    assert json_rows["document:DOC"]["acquired_sample"] is None
    assert json_rows["document:DOC"]["acquired_book"] == (
        "2022-03-04T05:06:07Z"
    )


def test_acquisition_properties_do_not_depend_on_event_order() -> None:
    early_sample = datetime(2024, 1, 1, tzinfo=timezone.utc)
    early_purchase = datetime(2024, 1, 2, tzinfo=timezone.utc)
    late_purchase = datetime(2024, 1, 3, tzinfo=timezone.utc)
    acquisition = BookAcquisition(
        key=CanonicalKey(asin="BOOK"),
        events=[
            AcquisitionEvent(AcquisitionEventType.KINDLE_PURCHASED, late_purchase),
            AcquisitionEvent(AcquisitionEventType.SAMPLE_ACQUIRED, early_sample),
            AcquisitionEvent(AcquisitionEventType.KINDLE_PURCHASED, early_purchase),
        ],
    )

    assert [event.timestamp for event in acquisition.events] == [
        early_sample,
        early_purchase,
        late_purchase,
    ]
    assert acquisition.acquired_sample == early_sample
    assert acquisition.acquired_book == early_purchase


def test_reconstructs_default_kindle_acquisition(tmp_path: Path) -> None:
    ownership = tmp_path / "Digital.Content.Ownership"
    ownership.mkdir()
    (ownership / "Digital.Content.Ownership.1.json").write_text(
        json.dumps(
            {
                "resource": {"ASIN": "DICTIONARY"},
                "rights": [
                    {
                        "rightType": "Download",
                        "acquiredDate": "2020-01-01T00:00:00Z",
                        "origin": {"originType": "KindleDictionary"},
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    acquisition = reconstruct_acquisitions(tmp_path)[0]

    assert str(acquisition.key) == "asin:DICTIONARY"
    assert acquisition.events[0].type == AcquisitionEventType.KINDLE_DEFAULT_ACQUIRED
