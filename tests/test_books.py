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
    ]
    assert rows[1][:3] == ["", "DOC1", "My Document"]
    assert "EntryCreationDate" not in rows[0]
