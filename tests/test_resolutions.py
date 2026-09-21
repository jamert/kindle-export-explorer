import csv
import json
from pathlib import Path

import pytest
from click.testing import CliRunner

from kindle_export_explorer.cli import main
from kindle_export_explorer.resolutions import (
    ReadStatus,
    load_resolutions,
    resolution_path,
)


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


def _write_book(
    root: Path,
    asin: str,
    title: str,
    acquired: str,
    *,
    sample: bool = False,
    default: bool = False,
) -> None:
    path = root / "Digital.Content.Ownership" / f"Digital.Content.Ownership.{asin}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "resource": {
                    "ASIN": asin,
                    "Product Name": title,
                    "resourceType": ("KindleEBookSample" if sample else "KindleEBook"),
                },
                "rights": [
                    {
                        "rightType": "Download",
                        "acquiredDate": acquired,
                        "origin": {
                            "originType": (
                                "Sample"
                                if sample
                                else "KindleDictionary"
                                if default
                                else "Purchase"
                            ),
                        },
                    },
                ],
            },
        ),
        encoding="utf-8",
    )


def _make_export(root: Path) -> None:
    _write_book(root, "OLD", "Older Book", "2024-01-01T00:00:00Z")
    _write_book(root, "NEW", "Newer Book", "2025-01-01T00:00:00Z")
    _write_book(
        root,
        "SAMPLE",
        "Sample Only",
        "2026-01-01T00:00:00Z",
        sample=True,
    )
    _write_book(
        root,
        "DEFAULT",
        "Default Dictionary",
        "2027-01-01T00:00:00Z",
        default=True,
    )
    _write_csv(
        root,
        "CustomerAuthorNameRelationship/CustomerAuthorNameRelationship.csv",
        ["ASIN", "Author Name"],
        [
            ["OLD", "Old Author"],
            ["NEW", "New Author"],
            ["SAMPLE", "Sample Author"],
            ["DEFAULT", "Dictionary Author"],
        ],
    )
    # Overview expects at least one recognized reading dataset.
    _write_csv(
        root,
        "Kindle.Devices.ReadingSession/Kindle.Devices.ReadingSession.csv",
        ["ASIN", "content_type"],
        [],
    )


def test_resolution_path_defaults_to_dot_config(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("XDG_CONFIG_HOME")
    monkeypatch.setenv("HOME", str(tmp_path))

    assert resolution_path() == (
        tmp_path / ".config" / "kindle-export-explorer" / "resolution.json"
    )


def test_resolve_reading_saves_each_answer_skips_existing_and_allows_override(
    tmp_path: Path,
) -> None:
    export = tmp_path / "Kindle"
    export.mkdir()
    _make_export(export)
    runner = CliRunner()
    help_result = runner.invoke(main, ["resolve-reading", "--help"])
    assert help_result.exit_code == 0
    assert "--include" in help_result.output
    assert "--exclude" in help_result.output
    assert "--keys" not in help_result.output

    # Input ends at the second prompt. The first answer must already be durable.
    interrupted = runner.invoke(main, ["resolve-reading", str(export)], input="Y\n")
    assert interrupted.exit_code == 1
    assert interrupted.output.index("Title: Newer Book") < interrupted.output.index(
        "Title: Older Book",
    )
    assert "Author: New Author" in interrupted.output
    assert "Acquired: 2025-01-01T00:00:00Z" in interrupted.output
    assert "Title: Sample Only" not in interrupted.output
    assert "Title: Default Dictionary" not in interrupted.output

    stored = load_resolutions()
    assert stored["asin:NEW"].resolution is ReadStatus.YES
    assert "asin:OLD" not in stored
    assert resolution_path() == (
        tmp_path / "config" / "kindle-export-explorer" / "resolution.json"
    )
    assert stored["asin:NEW"].updated_at.tzinfo is not None
    persisted = json.loads(resolution_path().read_text(encoding="utf-8"))
    assert set(persisted[0]) == {"key", "resolution", "updated_at"}
    assert persisted[0]["updated_at"].endswith("Z")

    # NEW is skipped. An invalid answer is rejected and Enter selects unknown.
    remaining = runner.invoke(
        main,
        ["resolve-reading", "--exclude", "NEW", str(export)],
        input="x\n\n",
    )
    assert remaining.exit_code == 0, remaining.output
    assert "Title: Newer Book" not in remaining.output
    assert "Title: Older Book" in remaining.output
    assert "Response is not recognized" in remaining.output
    assert load_resolutions()["asin:OLD"].resolution is ReadStatus.UNKNOWN

    complete = runner.invoke(main, ["resolve-reading", str(export)])
    assert complete.exit_code == 0
    assert (
        "all selected Kindle records already have a reading resolution"
        in complete.output
    )

    excluded = runner.invoke(
        main,
        [
            "resolve-reading",
            "--include",
            "NEW",
            "--exclude",
            "NEW",
            str(export),
        ],
    )
    assert excluded.exit_code == 0
    assert "no matching Kindle records found" in excluded.output

    explicit_sample = runner.invoke(
        main,
        ["resolve-reading", "--include", "SAMPLE", str(export)],
        input="u\n",
    )
    assert explicit_sample.exit_code == 0, explicit_sample.output
    assert "Title: Sample Only" in explicit_sample.output

    explicit_default = runner.invoke(
        main,
        ["resolve-reading", "--include", "DEFAULT", str(export)],
        input="u\n",
    )
    assert explicit_default.exit_code == 0, explicit_default.output
    assert "Title: Default Dictionary" in explicit_default.output

    # Explicitly included records are prompted again despite an existing decision.
    overridden = runner.invoke(
        main,
        ["resolve-reading", "--include", "NEW", str(export)],
        input="n\n",
    )
    assert overridden.exit_code == 0, overridden.output
    assert "Title: Newer Book" in overridden.output
    assert load_resolutions()["asin:NEW"].resolution is ReadStatus.NO

    partial = runner.invoke(
        main,
        ["resolve-reading", "--include", "asin:OLD", str(export)],
        input="P\n",
    )
    assert partial.exit_code == 0, partial.output
    assert load_resolutions()["asin:OLD"].resolution is ReadStatus.PARTIALLY


def test_overview_includes_manual_read_status(tmp_path: Path) -> None:
    export = tmp_path / "Kindle"
    export.mkdir()
    _make_export(export)
    runner = CliRunner()

    resolved = runner.invoke(
        main,
        ["resolve-reading", "--include", "NEW", str(export)],
        input="y\n",
    )
    assert resolved.exit_code == 0, resolved.output

    overview = runner.invoke(
        main,
        ["overview", "--json", "--include", "NEW", str(export)],
    )
    assert overview.exit_code == 0, overview.output
    assert json.loads(overview.output)["read_status"] == "yes"

    unresolved = runner.invoke(
        main,
        ["overview", "--json", "--include", "OLD", str(export)],
    )
    assert unresolved.exit_code == 0, unresolved.output
    assert json.loads(unresolved.output)["read_status"] is None
