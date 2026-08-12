import csv
import json
from pathlib import Path

from click.testing import CliRunner

from kindle_export_reassembly import main
from kindle_export_reassembly.books import reconstruct_books


def write_csv(root: Path, relative: str, headers: list[str], rows: list[list[str]]) -> None:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.writer(stream)
        writer.writerow(headers)
        writer.writerows(rows)


def test_reconstructs_and_enriches_books_without_activity_fields(tmp_path: Path) -> None:
    ownership = tmp_path / "Digital.Content.Ownership" / "Digital.Content.Ownership.1.json"
    ownership.parent.mkdir()
    ownership.write_text(
        json.dumps({"resource": {"ASIN": "BOOK1", "Product Name": "Old title"}}),
        encoding="utf-8",
    )
    write_csv(
        tmp_path,
        "uli/CustomerRelationshipIndex.1.csv",
        ["ASIN", "Product Name", "Resource Type", "Ownership Type", "Series Title", "Position In Collection"],
        [
            ["BOOK1", "A Book", "ITEM", "Item Owner", "A Series", "2"],
            ["WISH1", "A Wish", "ITEM", "Not Interested", "", ""],
        ],
    )
    write_csv(
        tmp_path,
        "uli/CustomerAuthorNameRelationship.1.csv",
        ["ASIN", "Author Name"],
        [["BOOK1", "Writer, Ada"], ["WISH1", "Ignored, Ira"]],
    )
    write_csv(
        tmp_path,
        "uli/CustomerGenres.1.csv",
        ["ASIN", "Genre"],
        [["BOOK1", "History"]],
    )
    write_csv(
        tmp_path,
        "Kindle.Devices.ReadingSession.csv",
        ["ASIN", "start_timestamp", "total_reading_millis"],
        [["ACTIVITY_ONLY", "2025-01-01", "1000"]],
    )

    books = reconstruct_books(tmp_path)

    assert len(books) == 1
    assert books[0].asin == "BOOK1"
    assert books[0].title == "A Book"
    assert books[0].authors == {"Writer, Ada"}
    assert books[0].genres == {"History"}
    assert books[0].series_title == "A Series"
    assert books[0].series_position == "2"


def test_default_kindle_content_is_hidden_unless_requested(tmp_path: Path) -> None:
    ownership = tmp_path / "Digital.Content.Ownership"
    ownership.mkdir()
    records = [
        ("DEFAULT-DICT", "Kindle Dictionary", "KindleDictionary"),
        ("DEFAULT-GUIDE", "Kindle User Guide", "KindleUserGuide"),
        ("BALLET", "Technical Manual and Dictionary of Classical Ballet", "Purchase"),
    ]
    for number, (asin, title, origin) in enumerate(records):
        (ownership / f"Digital.Content.Ownership.{number}.json").write_text(
            json.dumps(
                {
                    "resource": {"ASIN": asin, "Product Name": title},
                    "rights": [{"origin": {"originType": origin}}],
                }
            ),
            encoding="utf-8",
        )

    assert [book.asin for book in reconstruct_books(tmp_path)] == ["BALLET"]
    assert {book.asin for book in reconstruct_books(tmp_path, show_default=True)} == {
        "BALLET",
        "DEFAULT-DICT",
        "DEFAULT-GUIDE",
    }

    runner = CliRunner()
    default_result = runner.invoke(main, [str(tmp_path)])
    shown_result = runner.invoke(main, ["--show-default", str(tmp_path)])
    assert default_result.exit_code == 0
    assert "DEFAULT-DICT" not in default_result.output
    assert "DEFAULT-GUIDE" not in default_result.output
    assert "BALLET" in default_result.output
    assert "DEFAULT-DICT" in shown_result.output
    assert "DEFAULT-GUIDE" in shown_result.output


def test_samples_are_hidden_unless_requested_and_have_a_column(tmp_path: Path) -> None:
    ownership = tmp_path / "Digital.Content.Ownership"
    ownership.mkdir()
    records = [
        ("FULL", "A Full Book", "KindleEBook", "Purchase"),
        ("SAMPLE", "A Book Sample", "KindleEBookSample", "Sample"),
    ]
    for number, (asin, title, resource_type, origin) in enumerate(records):
        (ownership / f"Digital.Content.Ownership.{number}.json").write_text(
            json.dumps(
                {
                    "resource": {
                        "ASIN": asin,
                        "Product Name": title,
                        "resourceType": resource_type,
                    },
                    "rights": [{"origin": {"originType": origin}}],
                }
            ),
            encoding="utf-8",
        )

    assert [book.asin for book in reconstruct_books(tmp_path)] == ["FULL"]
    books = reconstruct_books(tmp_path, show_samples=True)
    assert {book.asin: book.is_sample for book in books} == {
        "FULL": False,
        "SAMPLE": True,
    }

    runner = CliRunner()
    default_result = runner.invoke(main, [str(tmp_path)])
    shown_result = runner.invoke(main, ["--show-samples", str(tmp_path)])
    assert default_result.exit_code == 0
    assert "SAMPLE" not in default_result.output
    assert "SAMPLE" in shown_result.output
    assert "\ttrue\n" in shown_result.output


def test_source_filters_kindle_and_print_from_provenance_not_asin(tmp_path: Path) -> None:
    ownership = tmp_path / "Digital.Content.Ownership"
    ownership.mkdir()
    (ownership / "Digital.Content.Ownership.1.json").write_text(
        json.dumps(
            {
                "resource": {
                    "ASIN": "1234567890",
                    "Product Name": "Numeric Kindle Book",
                    "resourceType": "KindleEBook",
                },
                "rights": [{"origin": {"originType": "Purchase"}}],
            }
        ),
        encoding="utf-8",
    )
    write_csv(
        tmp_path,
        "uli/CustomerRelationshipIndex.1.csv",
        ["ASIN", "Product Name", "Resource Type", "Ownership Type"],
        [
            ["1234567890", "Numeric Kindle Book", "ITEM", "Item Owner"],
            ["B0PRINT123", "Everything Fat Loss", "ITEM", "Item Owner"],
        ],
    )

    kindle = reconstruct_books(tmp_path)
    printed = reconstruct_books(tmp_path, source="print")
    all_books = reconstruct_books(tmp_path, source="all")
    assert [(book.asin, book.source) for book in kindle] == [("1234567890", "kindle")]
    assert [(book.asin, book.source) for book in printed] == [("B0PRINT123", "print")]
    assert len(all_books) == 2

    result = CliRunner().invoke(main, ["--source", "print", str(tmp_path)])
    assert result.exit_code == 0
    assert "Everything Fat Loss" in result.output
    assert "Numeric Kindle Book" not in result.output


def test_cli_prints_tsv_and_personal_documents(tmp_path: Path) -> None:
    write_csv(
        tmp_path,
        "Kindle.KindleDocs/datasets/Kindle.KindleDocs.DocumentMetadata.csv",
        ["DocumentId", "Title", "EntryCreationDate"],
        [["DOC1", "My Document", "2020-01-01"]],
    )

    result = CliRunner().invoke(main, [str(tmp_path)])

    assert result.exit_code == 0
    rows = list(csv.reader(result.output.splitlines(), dialect="excel-tab"))
    assert rows[0] == [
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
    assert rows[1][:3] == ["", "DOC1", "My Document"]
    assert "EntryCreationDate" not in rows[0]
