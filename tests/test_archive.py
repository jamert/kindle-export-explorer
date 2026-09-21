import csv
import io
import json
from contextlib import redirect_stdout
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile

import pytest
from click.testing import CliRunner

from kindle_export_explorer.cli import main
from scripts.explore_export import main as explore_main
from scripts.explore_export import profile_export
from scripts.explore_whispersync_read_state import main as whispersync_main


def _write_csv(
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


def _make_export(root: Path) -> None:
    ownership = root / "Digital.Content.Ownership"
    ownership.mkdir()
    (ownership / "Digital.Content.Ownership.1.json").write_text(
        json.dumps(
            {
                "resource": {
                    "ASIN": "BOOK1",
                    "Product Name": "Archive Test Book",
                    "resourceType": "KindleEBook",
                },
                "rights": [
                    {
                        "rightType": "Download",
                        "acquiredDate": "2025-01-02T03:04:05Z",
                        "origin": {"originType": "Purchase"},
                    },
                ],
            },
        ),
        encoding="utf-8",
    )
    _write_csv(
        root,
        "Kindle.Devices.ReadingSession/sessions.csv",
        [
            "ASIN",
            "content_type",
            "start_timestamp",
            "end_timestamp",
            "total_reading_millis",
            "number_of_page_flips",
        ],
        [
            [
                "BOOK1",
                "E-Book",
                "2025-02-01T10:00:00Z",
                "2025-02-01T10:05:00Z",
                "300000",
                "5",
            ],
        ],
    )
    _write_csv(
        root,
        "Digital.Content.Whispersync/whispersync.csv",
        ["ASIN", "Annotation Type", "Customer modified date on device", "Is Deleted"],
        [["BOOK1", "kindle.most_recent_read", "2025-02-01T10:05:00Z", "No"]],
    )


def _archive_directory(root: Path, archive: Path) -> None:
    with ZipFile(archive, "w", ZIP_DEFLATED) as output:
        for path in root.rglob("*"):
            if path.is_file():
                output.write(path, path.relative_to(root))


def test_all_commands_and_exploration_match_directory_and_archive(
    tmp_path: Path,
) -> None:
    root = tmp_path / "Kindle"
    root.mkdir()
    _make_export(root)
    archive = tmp_path / "Kindle.zip"
    _archive_directory(root, archive)

    runner = CliRunner()
    commands = [
        ["books", "--acquisition"],
        ["overview"],
        ["reading", "asin:BOOK1"],
    ]
    for command in commands:
        directory_result = runner.invoke(main, [*command, str(root)])
        archive_result = runner.invoke(main, [*command, str(archive)])
        assert directory_result.exit_code == 0, directory_result.output
        assert archive_result.exit_code == 0, archive_result.output
        assert archive_result.output == directory_result.output

    assert profile_export(archive) == profile_export(root)

    directory_output = io.StringIO()
    with redirect_stdout(directory_output):
        whispersync_main([str(root)])
    archive_output = io.StringIO()
    with redirect_stdout(archive_output):
        whispersync_main([str(archive)])
    assert archive_output.getvalue() == directory_output.getvalue()


def test_environment_path_and_explicit_path_priority(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "Kindle"
    root.mkdir()
    _make_export(root)
    archive = tmp_path / "Kindle.zip"
    _archive_directory(root, archive)
    missing = tmp_path / "missing.zip"

    runner = CliRunner()
    commands = [
        ["books", "--acquisition"],
        ["overview"],
        ["reading", "asin:BOOK1"],
    ]
    for command in commands:
        expected = runner.invoke(main, [*command, str(root)])
        from_environment = runner.invoke(
            main,
            command,
            env={"KINDLE_EXPORT_PATH": str(archive)},
        )
        explicit_override = runner.invoke(
            main,
            [*command, str(root)],
            env={"KINDLE_EXPORT_PATH": str(missing)},
        )
        assert expected.exit_code == 0, expected.output
        assert from_environment.exit_code == 0, from_environment.output
        assert explicit_override.exit_code == 0, explicit_override.output
        assert from_environment.output == expected.output
        assert explicit_override.output == expected.output

    absent = runner.invoke(main, ["books"])
    assert absent.exit_code == 2
    assert "KINDLE_EXPORT_PATH" in absent.output

    for script in (explore_main, whispersync_main):
        monkeypatch.setenv("KINDLE_EXPORT_PATH", str(missing))
        explicit_output = io.StringIO()
        with redirect_stdout(explicit_output):
            result = script([str(root)])
        assert result in {None, 0}

        monkeypatch.setenv("KINDLE_EXPORT_PATH", str(archive))
        environment_output = io.StringIO()
        with redirect_stdout(environment_output):
            result = script([])
        assert result in {None, 0}
        assert environment_output.getvalue() == explicit_output.getvalue()
