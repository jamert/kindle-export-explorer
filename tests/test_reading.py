import csv
import json
from datetime import date, datetime, timezone
from pathlib import Path

from click.testing import CliRunner

from kindle_export_reassembly import reconstruct_reading
from kindle_export_reassembly.reading_cli import main as reading_main


def write_csv(
    root: Path,
    relative: str,
    headers: list[str],
    rows: list[list[str]],
) -> None:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.writer(stream)
        writer.writerow(headers)
        writer.writerows(rows)


def test_collects_source_records_by_canonical_key(tmp_path: Path) -> None:
    ownership = tmp_path / "Digital.Content.Ownership"
    ownership.mkdir()
    (ownership / "Digital.Content.Ownership.1.json").write_text(
        json.dumps(
            {
                "resource": {
                    "ASIN": "BOOK",
                    "Product Name": "The Book Title",
                    "resourceType": "KindleEBook",
                },
                "rights": [
                    {
                        "rightType": "Download",
                        "origin": {"originType": "Purchase"},
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    write_csv(
        tmp_path,
        "Kindle.Devices.ReadingSession/Kindle.Devices.ReadingSession.csv",
        [
            "ASIN",
            "start_timestamp",
            "end_timestamp",
            "content_type",
            "total_reading_millis",
            "number_of_page_flips",
        ],
        [
            [
                "BOOK",
                "2024-01-01T10:00:00Z",
                "2024-01-01T10:05:00Z",
                "E-Book Sample",
                "299500",
                "12",
            ],
            [
                "BOOK",
                "2024-01-01T10:06:00Z",
                "2024-01-01T10:07:00Z",
                "E-Book",
                "0",
                "0",
            ],
            ["Not Available", "", "2024-01-01T11:00:00Z", "SIDE", "", ""],
        ],
    )
    write_csv(
        tmp_path,
        "Kindle.ReadingInsights/Kindle.reading-insights-sessions_with_adjustments.csv",
        [
            "ASIN",
            "start_time",
            "end_time",
            "total_reading_milliseconds",
            "product_name",
        ],
        [
            [
                "BOOK",
                "2024-01-01T10:00:00.500Z",
                "2024-01-01T10:05:00Z",
                "299500.0",
                "Book",
            ]
        ],
    )
    write_csv(
        tmp_path,
        "Digital.Content.Whispersync/whispersync.csv",
        [
            "ASIN",
            "Non ASIN",
            "Annotation Type",
            "ContentType",
            "Creation Date",
            "Customer modified date on device",
            "LastUpdatedDate",
            "Is Deleted",
        ],
        [
            [
                "BOOK",
                "",
                "kindle.last_read",
                "EBSP",
                "2024-01-01T10:05:00Z",
                "2024-01-01T10:05:01Z",
                "2024-01-01T10:05:02Z",
                "No",
            ],
            [
                "BOOK",
                "",
                "kindle.highlight",
                "EBSP",
                "2024-01-01T10:03:00Z",
                "2024-01-01T10:03:01Z",
                "2024-01-01T10:03:02Z",
                "No",
            ],
            [
                "",
                "DOC-ID",
                "kindle.most_recent_read",
                "PDOC",
                "2023-01-01T00:00:00Z",
                "2023-01-02T00:00:00Z",
                "2023-01-02T00:00:01Z",
                "Yes",
            ],
            ["", "unmatched-uuid", "kindle.last_read", "EBOK", "", "", "", "No"],
        ],
    )
    write_csv(
        tmp_path,
        "Kindle.Devices.ReadingActionsContainers/Kindle.Devices.ReadingActionsContainers.csv",
        [
            "ASIN",
            "created_timestamp",
            "display_start",
            "display_end",
            "reading_action_display",
            "entry_point",
        ],
        [
            [
                "BOOK",
                "2024-01-01T10:05:03Z",
                "2024-01-01T10:05:00Z",
                "2024-01-01T10:05:03Z",
                "Before you go BSE",
                "Reach end of book",
            ]
        ],
    )
    write_csv(
        tmp_path,
        "Kindle.Devices.autoMarkAsRead/Kindle.Devices.autoMarkAsRead.csv",
        ["active_ASIN", "created_timestamp", "file_auto_marked_as_read"],
        [["BOOK", "2024-01-01T10:05:05Z", "BOOK"]],
    )
    write_csv(
        tmp_path,
        "Kindle.ReadingInsights/Kindle.UserUniqueTitlesCompleted.csv",
        ["asin_date_and_content_type", "product_name", "personal_document_id"],
        [
            ["BOOK_2024-01-01_AUTOMATIC", "Book", ""],
            ["invalid", "Ignored", ""],
        ],
    )

    readings = reconstruct_reading(tmp_path)
    by_key = {str(reading.key): reading for reading in readings}

    assert set(by_key) == {"asin:BOOK", "document:DOC-ID"}
    book = by_key["asin:BOOK"]
    assert len(book.device_sessions) == 2
    assert book.device_sessions[0].content_type == "E-Book Sample"
    assert book.device_sessions[0].start == datetime(
        2024, 1, 1, 10, tzinfo=timezone.utc
    )
    assert book.device_sessions[0].total_reading_millis == 299500
    assert book.device_sessions[0].number_of_page_flips == 12
    assert book.device_sessions_summary.start == datetime(
        2024, 1, 1, 10, tzinfo=timezone.utc
    )
    assert book.device_sessions_summary.end == datetime(
        2024, 1, 1, 10, 7, tzinfo=timezone.utc
    )
    assert book.device_sessions_summary.total_reading_millis == 299500
    assert book.device_sessions_summary.total_reading_humanized == "5m"
    assert book.device_sessions_summary.total_page_flips == 12
    assert book.device_sessions_summary.total_count == 2
    assert book.device_sessions_summary.non_zero_count == 1
    assert len(book.insights_sessions) == 1
    assert book.insights_sessions[0].start == datetime(
        2024, 1, 1, 10, 0, 0, 500000, tzinfo=timezone.utc
    )
    assert len(book.whispersync_records) == 1
    assert book.whispersync_records[0].content_type == "EBSP"
    assert len(book.reading_action_containers) == 1
    assert book.reading_action_containers[0].entry_point == "Reach end of book"
    assert len(book.auto_mark_as_read_records) == 1
    assert len(book.completion_records) == 1
    assert book.completion_records[0].completed_on == date(2024, 1, 1)
    assert book.completion_records[0].completion_type == "AUTOMATIC"

    document = by_key["document:DOC-ID"]
    assert len(document.whispersync_records) == 1
    assert not document.device_sessions

    result = CliRunner().invoke(
        reading_main,
        ["asin:BOOK", str(tmp_path)],
    )
    assert result.exit_code == 0
    output = json.loads(result.output)
    assert output["key"] == "asin:BOOK"
    assert output["title"] == "The Book Title"
    assert output["device_sessions"][0]["start"] == "2024-01-01T10:00:00+00:00"
    assert output["device_sessions"][0]["content_type"] == "E-Book Sample"
    assert output["device_sessions_summary"] == {
        "start": "2024-01-01T10:00:00+00:00",
        "end": "2024-01-01T10:07:00+00:00",
        "total_reading_millis": 299500,
        "total_reading_humanized": "5m",
        "total_page_flips": 12,
        "total_count": 2,
        "non_zero_count": 1,
    }
    assert "asin" not in output["device_sessions"][0]
    assert "preferred_marketplace" not in output["device_sessions"][0]
    assert "purchased_marketplace" not in output["device_sessions"][0]
    assert "device_family" not in output["device_sessions"][0]
    assert "device_serial_number" not in output["device_sessions"][0]
    assert "device_software_version" not in output["device_sessions"][0]
    assert len(output["insights_sessions"]) == 1
    assert "asin" not in output["insights_sessions"][0]
    assert "product_name" not in output["insights_sessions"][0]
    assert "reading_marketplace" not in output["insights_sessions"][0]
    assert len(output["whispersync_records"]) == 1
    assert "asin" not in output["whispersync_records"][0]
    assert "non_asin" not in output["whispersync_records"][0]
    assert "third_party_device" not in output["whispersync_records"][0]
    assert output["completion_records"][0]["completed_on"] == "2024-01-01"
    assert "product_name" not in output["completion_records"][0]
    assert "personal_document_id" not in output["completion_records"][0]
