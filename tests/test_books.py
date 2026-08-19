import csv
import json
from pathlib import Path

from click.testing import CliRunner

from kindle_export_reassembly import (
    CanonicalizationService,
    CanonicalKey,
    DocumentRecord,
    KindleBookRecord,
    PrintBookRecord,
    main,
)
from kindle_export_reassembly.books import normalize_sharded_path, reconstruct_books


def write_csv(root: Path, relative: str, headers: list[str], rows: list[list[str]]) -> None:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.writer(stream)
        writer.writerow(headers)
        writer.writerows(rows)


def test_canonical_key_string_forms() -> None:
    assert str(CanonicalKey(asin="BOOK", sample=False)) == "asin:ebook:BOOK"
    assert str(CanonicalKey(asin="BOOK", sample=True)) == "asin:sample:BOOK"
    assert str(CanonicalKey(document_id="DOC")) == "document:DOC"


def test_canonicalization_service_keeps_source_record_types_separate() -> None:
    ebook = KindleBookRecord(asin="BOOK")
    ebook.metadata.title = "Full Book"
    sample = KindleBookRecord(asin="BOOK", sample=True)
    sample.metadata.title = "Sample"
    printed = PrintBookRecord(asin="PRINT")
    printed.metadata.title = "Printed"
    document = DocumentRecord(
        document_id="DOC",
        title="Document",
        provider="Provider",
    )

    kindle = CanonicalizationService.convert_kindle(ebook, sample)
    assert [str(book.key) for book in kindle] == [
        "asin:ebook:BOOK",
        "asin:sample:BOOK",
    ]
    assert str(CanonicalizationService.convert_print(printed).key) == (
        "asin:ebook:PRINT"
    )
    canonical_document = CanonicalizationService.convert_document(document)
    assert str(canonical_document.key) == "document:DOC"
    assert canonical_document.authors.names == ["Provider"]
    assert not hasattr(document, "genres")
    assert not hasattr(document, "marketplaces")


def test_normalize_sharded_path_only_collapses_real_shard_groups(tmp_path: Path) -> None:
    shards = tmp_path / "Digital.Content.Ownership"
    shards.mkdir()
    first = shards / "Digital.Content.Ownership.1.json"
    second = shards / "Digital.Content.Ownership.2.json"
    first.touch()
    second.touch()

    versioned = tmp_path / "dataset" / "CustomerGenres.17.42.csv"
    versioned.parent.mkdir()
    versioned.touch()

    assert normalize_sharded_path(tmp_path, first) == (
        "Digital.Content.Ownership/shard.json"
    )
    assert normalize_sharded_path(tmp_path, second) == (
        "Digital.Content.Ownership/shard.json"
    )
    assert normalize_sharded_path(tmp_path, versioned) == (
        "dataset/CustomerGenres.17.42.csv"
    )

    datasets = tmp_path / "datasets"
    partition_paths = []
    versions = (f"{major}.{minor}" for major, minor in zip(range(3), range(10, 13)))
    for version in versions:
        directory = datasets / f"CustomerAuthor.{version}"
        directory.mkdir(parents=True)
        partition = directory / f"CustomerAuthor.{version}.csv"
        partition.touch()
        partition_paths.append(partition)
    for partition in partition_paths:
        assert normalize_sharded_path(tmp_path, partition) == (
            "datasets/CustomerAuthor.*/*.csv"
        )


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
    assert books[0].authors.names == ["Writer, Ada"]
    assert books[0].authors.asin == "BOOK1"
    assert books[0].genres == ["History"]
    assert books[0].series.title == "A Series"
    assert books[0].series.position == "2"


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
    assert {book.asin: book.key.sample for book in books} == {
        "FULL": False,
        "SAMPLE": True,
    }

    runner = CliRunner()
    default_result = runner.invoke(main, [str(tmp_path)])
    shown_result = runner.invoke(main, ["--show-samples", str(tmp_path)])
    assert default_result.exit_code == 0
    assert "SAMPLE" not in default_result.output
    assert "SAMPLE" in shown_result.output
    rows = list(csv.DictReader(shown_result.output.splitlines(), dialect="excel-tab"))
    sample = next(row for row in rows if row["key"] == "asin:sample:SAMPLE")
    assert sample["ownership_digital"] == "kindle_sample"


def test_sample_and_ebook_are_deduplicated_by_synthetic_key(tmp_path: Path) -> None:
    ownership = tmp_path / "Digital.Content.Ownership"
    ownership.mkdir()
    records = [
        ("KindleEBookSample", "Sample", "2024-01-01T00:00:00Z"),
        ("KindleEBook", "Purchase", "2024-01-02T00:00:00Z"),
    ]
    for number, (resource_type, origin_type, acquired) in enumerate(records, 1):
        (ownership / f"Digital.Content.Ownership.{number}.json").write_text(
            json.dumps(
                {
                    "resource": {
                        "ASIN": "BOTH",
                        "Product Name": "Both Editions",
                        "resourceType": resource_type,
                    },
                    "rights": [
                        {
                            "origin": {"originType": origin_type},
                            "acquiredDate": acquired,
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
            "Product Name",
            "Resource Type",
            "Ownership Type",
            "Sortable Author Name",
        ],
        [["BOTH", "Both Editions", "ITEM", "Item Owner", "Writer, Primary"]],
    )
    write_csv(
        tmp_path,
        "uli/CustomerAuthorNameRelationship.csv",
        ["ASIN", "Author Name"],
        [
            ["BOTH", "Writer, Primary"],
            ["BOTH", "Writer, Second"],
        ],
    )

    default_books = reconstruct_books(tmp_path)
    assert [str(book.key) for book in default_books] == ["asin:ebook:BOTH"]

    all_books = reconstruct_books(tmp_path, show_samples=True)
    assert {str(book.key) for book in all_books} == {
        "asin:ebook:BOTH",
        "asin:sample:BOTH",
    }
    assert {str(book.key): book.key.sample for book in all_books} == {
        "asin:ebook:BOTH": False,
        "asin:sample:BOTH": True,
    }
    assert {tuple(book.authors.names) for book in all_books} == {
        ("Writer, Primary", "Writer, Second")
    }

    result = CliRunner().invoke(main, ["--show-samples", str(tmp_path)])
    rows = list(csv.DictReader(result.output.splitlines(), dialect="excel-tab"))
    assert [row["key"] for row in rows] == ["asin:ebook:BOTH", "asin:sample:BOTH"]


def test_extra_adds_series_genres_and_marketplace(tmp_path: Path) -> None:
    write_csv(
        tmp_path,
        "uli/CustomerRelationshipIndex.1.csv",
        [
            "ASIN",
            "Product Name",
            "Resource Type",
            "Ownership Type",
            "Series Title",
            "Position In Collection",
            "Marketplace",
        ],
        [["PRINT", "Print Book", "ITEM", "Item Owner", "Series", "2", "www.amazon.com"]],
    )
    write_csv(
        tmp_path,
        "uli/CustomerGenres.1.csv",
        ["ASIN", "Genre"],
        [["PRINT", "History"]],
    )
    write_csv(
        tmp_path,
        "saga/CollectionRightsDatastore.csv",
        [
            "record-type",
            "series-ASIN",
            "series-product-name",
            "item-ASIN",
            "item-product-name",
            "item-position-in-series",
        ],
        [["Item", "SERIES-1", "Saga Series", "PRINT", "Print Book", "3"]],
    )

    runner = CliRunner()
    plain = runner.invoke(main, ["--source", "all", str(tmp_path)])
    extra = runner.invoke(main, ["--extra", "--source", "all", str(tmp_path)])
    assert plain.exit_code == 0
    assert extra.exit_code == 0
    assert plain.output.splitlines()[0].split("\t") == [
        "key",
        "title",
        "author",
        "ownership_digital",
        "ownership_print",
    ]
    row = next(csv.DictReader(extra.output.splitlines(), dialect="excel-tab"))
    assert row["series_title"] == "Saga Series"
    assert row["series_asin"] == "SERIES-1"
    assert row["series_position"] == "3"
    assert row["genres"] == "History"
    assert row["marketplace"] == "www.amazon.com"

    removed_raw = runner.invoke(main, ["--raw", str(tmp_path)])
    assert removed_raw.exit_code != 0
    assert "No such option '--raw'" in removed_raw.output


def test_canonical_uses_sortable_author_only_as_fallback(
    tmp_path: Path,
) -> None:
    write_csv(
        tmp_path,
        "uli/CustomerRelationshipIndex.1.csv",
        [
            "ASIN",
            "Product Name",
            "Resource Type",
            "Ownership Type",
            "Sortable Title",
            "Sortable Author Name",
            "Marketplace",
        ],
        [
            [
                "PRINT",
                "The Example",
                "ITEM",
                "Item Owner",
                "Example, The",
                "Writer, Ada",
                "www.amazon.co.uk",
            ],
            [
                "PRINT",
                "The Example",
                "ITEM",
                "Item Owner",
                "Example, The",
                "Writer, Ada",
                "www.amazon.com",
            ],
        ],
    )

    result = CliRunner().invoke(main, ["--extra", "--source", "all", str(tmp_path)])
    assert result.exit_code == 0
    row = next(csv.DictReader(result.output.splitlines(), dialect="excel-tab"))
    assert row["title"] == "Example, The"
    assert row["author"] == "Writer, Ada"
    assert row["marketplace"] == "www.amazon.com"
    assert "multiple marketplaces" in result.stderr
    assert "using www.amazon.com" in result.stderr


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
    assert [(book.asin, book.ownership_digital) for book in kindle] == [
        ("1234567890", "kindle_ebook")
    ]
    assert [(book.asin, book.ownership_print) for book in printed] == [
        ("B0PRINT123", True)
    ]
    assert len(all_books) == 2

    result = CliRunner().invoke(main, ["--source", "print", str(tmp_path)])
    assert result.exit_code == 0
    assert "Everything Fat Loss" in result.output
    assert "Numeric Kindle Book" not in result.output


def test_activity_and_content_update_tables_are_ignored(tmp_path: Path) -> None:
    ownership = tmp_path / "Digital.Content.Ownership"
    ownership.mkdir()
    (ownership / "Digital.Content.Ownership.1.json").write_text(
        json.dumps(
            {
                "resource": {
                    "ASIN": "BOOK",
                    "Product Name": "Real Book",
                    "resourceType": "KindleEBook",
                },
                "rights": [{"origin": {"originType": "Purchase"}}],
            }
        ),
        encoding="utf-8",
    )
    write_csv(
        tmp_path,
        "Digital.Content.Whispersync/whispersync.csv",
        ["ASIN", "Product Name", "ContentType", "Format"],
        [["ACTIVITY", "Activity Book", "EBOK", "Mobi8"]],
    )
    write_csv(
        tmp_path,
        "Kindle.KindleContentUpdate/Kindle.KindleContentUpdate.ContentUpdates.csv",
        ["ASIN", "Product Name", "Current Book Format", "New Book Format"],
        [["UPDATE", "Updated Book", "Mobi", "Enhanced TypeSetting"]],
    )
    write_csv(
        tmp_path,
        "Kindle.ReadingInsights/Kindle.UserUniqueTitlesCompleted.csv",
        ["asin_date_and_content_type", "personal_document_id", "product_name"],
        [["COMPLETED_2025-01-01_AUTOMATIC", "", "Completed Book"]],
    )

    result = CliRunner().invoke(main, ["--extra", str(tmp_path)])
    assert result.exit_code == 0
    rows = list(csv.DictReader(result.output.splitlines(), dialect="excel-tab"))
    assert [row["key"] for row in rows] == ["asin:ebook:BOOK"]
    assert "ACTIVITY" not in result.output
    assert "UPDATE" not in result.output
    assert "COMPLETED" not in result.output


def test_json_and_jsonl_aliases_print_json_lines_with_native_types(tmp_path: Path) -> None:
    ownership = tmp_path / "Digital.Content.Ownership"
    ownership.mkdir()
    (ownership / "Digital.Content.Ownership.1.json").write_text(
        json.dumps(
            {
                "resource": {
                    "ASIN": "SAMPLE",
                    "Product Name": "Échantillon",
                    "resourceType": "KindleEBookSample",
                },
                "rights": [{"origin": {"originType": "Sample"}}],
            }
        ),
        encoding="utf-8",
    )

    runner = CliRunner()
    for option in ("--json", "--jsonl"):
        result = runner.invoke(main, [option, "--show-samples", "--extra", str(tmp_path)])
        assert result.exit_code == 0
        lines = result.output.splitlines()
        assert len(lines) == 1
        record = json.loads(lines[0])
        assert record["key"] == "asin:sample:SAMPLE"
        assert record["title"] == "Échantillon"
        assert record["author"] == {"names": [], "asin": "SAMPLE"}
        assert record["ownership_digital"] == "kindle_sample"
        assert record["ownership_print"] is False
        assert record["genres"] == []


def test_include_filter_accepts_asins_and_document_ids(tmp_path: Path) -> None:
    ownership = tmp_path / "Digital.Content.Ownership"
    ownership.mkdir()
    for number, asin in enumerate(("BOOK1", "BOOK2", "BOOK3"), 1):
        (ownership / f"Digital.Content.Ownership.{number}.json").write_text(
            json.dumps(
                {
                    "resource": {
                        "ASIN": asin,
                        "Product Name": f"Title {number}",
                        "resourceType": "KindleEBook",
                    },
                    "rights": [{"origin": {"originType": "Purchase"}}],
                }
            ),
            encoding="utf-8",
        )

    (ownership / "Digital.Content.Ownership.4.json").write_text(
        json.dumps(
            {
                "resource": {
                    "ASIN": "SAMPLE",
                    "Product Name": "Sample Book",
                    "resourceType": "KindleEBookSample",
                },
                "rights": [{"origin": {"originType": "Sample"}}],
            }
        ),
        encoding="utf-8",
    )
    write_csv(
        tmp_path,
        "Kindle.KindleDocs.DocumentMetadata.csv",
        ["DocumentId", "Title", "Filename"],
        [["DOC-123", "Personal Document", "document.pdf"]],
    )
    write_csv(
        tmp_path,
        "uli/CustomerRelationshipIndex.csv",
        ["ASIN", "Product Name", "Resource Type", "Ownership Type"],
        [["PRINT", "Print Book", "ITEM", "Item Owner"]],
    )

    runner = CliRunner()
    tsv_result = runner.invoke(
        main,
        ["--include", " book1,DOC-123,SAMPLE,PRINT ", str(tmp_path)],
    )
    assert tsv_result.exit_code == 0
    tsv_rows = list(csv.DictReader(tsv_result.output.splitlines(), dialect="excel-tab"))
    assert {row["key"] for row in tsv_rows} == {
        "asin:ebook:BOOK1",
        "asin:sample:SAMPLE",
        "asin:ebook:PRINT",
        "document:DOC-123",
    }
    sample = next(row for row in tsv_rows if row["key"] == "asin:sample:SAMPLE")
    assert sample["ownership_digital"] == "kindle_sample"
    printed = next(row for row in tsv_rows if row["key"] == "asin:ebook:PRINT")
    assert printed["ownership_digital"] == "unknown"
    assert printed["ownership_print"] == "true"

    json_result = runner.invoke(
        main, ["--jsonl", "--include", "BOOK2,missing", str(tmp_path)]
    )
    assert json_result.exit_code == 0
    json_rows = [json.loads(line) for line in json_result.output.splitlines()]
    assert [row["key"] for row in json_rows] == ["asin:ebook:BOOK2"]

    empty_result = runner.invoke(main, ["--include", " , ", str(tmp_path)])
    assert empty_result.exit_code != 0
    assert "provide at least one key, ASIN, or document ID" in empty_result.output

    removed_result = runner.invoke(main, ["--asin", "BOOK1", str(tmp_path)])
    assert removed_result.exit_code != 0
    assert "No such option '--asin'" in removed_result.output


def test_exclude_filter_accepts_asins_and_document_ids(tmp_path: Path) -> None:
    ownership = tmp_path / "Digital.Content.Ownership"
    ownership.mkdir()
    for number, asin in enumerate(("BOOK1", "BOOK2", "BOOK3"), 1):
        (ownership / f"Digital.Content.Ownership.{number}.json").write_text(
            json.dumps(
                {
                    "resource": {
                        "ASIN": asin,
                        "Product Name": f"Title {number}",
                        "resourceType": "KindleEBook",
                    },
                    "rights": [{"origin": {"originType": "Purchase"}}],
                }
            ),
            encoding="utf-8",
        )
    write_csv(
        tmp_path,
        "Kindle.KindleDocs.DocumentMetadata.csv",
        ["DocumentId", "Title", "Filename"],
        [["DOC-123", "Personal Document", "document.pdf"]],
    )

    runner = CliRunner()
    tsv_result = runner.invoke(
        main, ["--exclude", " book1,DOC-123,missing ", str(tmp_path)]
    )
    assert tsv_result.exit_code == 0
    tsv_rows = list(csv.DictReader(tsv_result.output.splitlines(), dialect="excel-tab"))
    assert {row["key"] for row in tsv_rows} == {
        "asin:ebook:BOOK2",
        "asin:ebook:BOOK3",
    }

    json_result = runner.invoke(
        main,
        [
            "--jsonl",
            "--include",
            "BOOK1,BOOK2,DOC-123",
            "--exclude",
            "BOOK2,DOC-123",
            str(tmp_path),
        ],
    )
    assert json_result.exit_code == 0
    json_rows = [json.loads(line) for line in json_result.output.splitlines()]
    assert [row["key"] for row in json_rows] == ["asin:ebook:BOOK1"]

    unknown_result = runner.invoke(main, ["--exclude", "missing", str(tmp_path)])
    assert unknown_result.exit_code == 0
    unknown_rows = list(
        csv.DictReader(unknown_result.output.splitlines(), dialect="excel-tab")
    )
    assert len(unknown_rows) == 4

    empty_result = runner.invoke(main, ["--exclude", " , ", str(tmp_path)])
    assert empty_result.exit_code != 0
    assert "provide at least one key, ASIN, or document ID" in empty_result.output


def test_cloud_drive_notice_is_default_content_by_portable_metadata(
    tmp_path: Path,
) -> None:
    write_csv(
        tmp_path,
        "Kindle.KindleDocs.DocumentMetadata.csv",
        ["DocumentId", "Title", "DocumentProvider", "Filename"],
        [
            [
                "NOTICE-ID",
                "Notice From Amazon Cloud Drive",
                "Amazon Cloud Drive",
                "Notice From Amazon Cloud Drive.docx",
            ],
            ["USER-ID", "My Document", "Amazon Cloud Drive", "my-document.docx"],
        ],
    )

    default_books = reconstruct_books(tmp_path)
    assert [str(book.key) for book in default_books] == ["document:USER-ID"]

    shown_books = reconstruct_books(tmp_path, show_default=True)
    assert {str(book.key) for book in shown_books} == {
        "document:NOTICE-ID",
        "document:USER-ID",
    }
    assert all(book.genres == [] for book in shown_books)

    runner = CliRunner()
    included = runner.invoke(main, ["--include", "NOTICE-ID", str(tmp_path)])
    assert included.exit_code == 0
    assert "Notice From Amazon Cloud Drive" in included.output
    included_row = next(
        csv.DictReader(included.output.splitlines(), dialect="excel-tab")
    )
    assert included_row["author"] == "Amazon Cloud Drive"
    assert included_row["ownership_digital"] == "default"

    excluded = runner.invoke(
        main,
        ["--include", "NOTICE-ID", "--exclude", "NOTICE-ID", str(tmp_path)],
    )
    assert excluded.exit_code == 0
    assert "Notice From Amazon Cloud Drive" not in excluded.output


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
        "key",
        "title",
        "author",
        "ownership_digital",
        "ownership_print",
    ]
    assert rows[1] == [
        "document:DOC1",
        "My Document",
        "",
        "personal_document",
        "false",
    ]
