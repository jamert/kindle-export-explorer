import io
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1]))

from scripts.explore_export import profile_export, write_markdown


def test_profiles_shards_csv_columns_and_json_arrays(tmp_path: Path) -> None:
    shards = tmp_path / "ownership"
    shards.mkdir()
    for number, asin in enumerate(("A", "B"), 1):
        (shards / f"records.{number}.json").write_text(
            json.dumps(
                {
                    "asin": asin,
                    "rights": [
                        {"type": "Download"},
                        {"type": "Read" if number == 1 else "Download"},
                    ],
                }
            ),
            encoding="utf-8",
        )

    csv_path = tmp_path / "metadata.csv"
    csv_path.write_text("id,kind,optional\n1,book,\n2,book,value\n", encoding="utf-8")

    (tmp_path / "ignored.txt").write_text("ignored", encoding="utf-8")

    profiles = profile_export(tmp_path)
    by_path = {profile.path: profile for profile in profiles}
    shard = by_path["ownership/shard.json"]
    assert shard.record_count == 2
    assert len(shard.physical_files) == 2
    assert shard.columns["asin"].value_count == 2
    assert shard.columns["asin"].values == {"A", "B"}
    assert shard.columns["rights[].type"].value_count == 4
    assert shard.columns["rights[].type"].values == {"Download", "Read"}

    metadata = by_path["metadata.csv"]
    assert metadata.columns["optional"].populated_records == 1
    assert metadata.columns["optional"].value_count == 1

    output = io.StringIO()
    write_markdown(profiles, output)
    report = output.getvalue()
    assert "## Table of contents" in report
    assert "- [`metadata.csv`](#dataset-1)" in report
    assert "- [`ownership/*.json`](#dataset-2)" in report
    assert '<a id="dataset-2"></a>' in report
    assert "ignored.txt" not in report
    assert "Unsupported files" not in report
    assert "## `ownership/*.json`" in report
    assert "## `ownership/shard.json`" not in report
    assert "- Shards: `ownership/*.json`" in report
    assert "records.1.json" not in report
    assert "records.2.json" not in report
    assert "| `id` | 2 | 2 | 2 | 0 | yes |" in report
    assert 'all: "book"' in report
