#!/usr/bin/env python3
"""Profile every CSV and JSON dataset in an Amazon Kindle export.

Numbered file shards are grouped under the same normalized path used by the main
application. The report is Markdown so it is both readable and easy to archive.
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from collections.abc import Iterable, Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import TextIO

from kindle_export_reassembly.books import clean, normalize_sharded_path


LOW_CARDINALITY_LIMIT = 10
EXAMPLE_LIMIT = 5
EXAMPLE_LENGTH_LIMIT = 160

BOOK_PATH_MARKERS = (
    "Digital.Content.Ownership/",
    "Digital.SeriesContent.Relation.2/BookRelation.csv",
    "Kindle.KindleDocs.DocumentMetadata/",
    "Kindle.SagaSeriesInfra.CollectionRightsDatastore/",
    "Kindle.UnifiedLibraryIndex.CustomerAuthorIdRelationship.",
    "Kindle.UnifiedLibraryIndex.CustomerAuthorNameRelationship.",
    "Kindle.UnifiedLibraryIndex.CustomerRelationshipIndex.",
)
ACQUISITION_PATH_MARKERS = (
    "Digital.Content.Ownership/",
    "Kindle.UnifiedLibraryIndex.CustomerRelationshipIndex.",
)
READING_PATH_MARKERS = (
    "Digital.Content.Whispersync/",
    "Kindle.Devices.autoMarkAsRead/",
    "Kindle.Devices.ReadingActionsContainers/",
    "Kindle.Devices.ReadingSession/",
    "reading-insights-sessions_with_adjustments",
)


@dataclass
class ColumnProfile:
    """Statistics for one flattened source column."""

    value_count: int = 0
    populated_records: int = 0
    values: set[str] = field(default_factory=set)
    examples: list[str] = field(default_factory=list)

    def add_record_values(self, values: Iterable[object]) -> None:
        nonempty = [value for value in (clean(value) for value in values) if value]
        if not nonempty:
            return
        self.populated_records += 1
        self.value_count += len(nonempty)
        for value in nonempty:
            if value not in self.values and len(self.examples) < EXAMPLE_LIMIT:
                self.examples.append(value)
            self.values.add(value)


@dataclass
class DatasetProfile:
    """Statistics for all records represented by one normalized source path."""

    path: str
    physical_files: set[str] = field(default_factory=set)
    record_count: int = 0
    columns: dict[str, ColumnProfile] = field(default_factory=dict)

    def add_record(self, record: dict[str, list[object]]) -> None:
        self.record_count += 1
        for name, values in record.items():
            self.columns.setdefault(name, ColumnProfile()).add_record_values(values)


def _flatten_json(value: object, prefix: str = "") -> dict[str, list[object]]:
    """Flatten a JSON record while preserving repeated values from arrays."""
    result: dict[str, list[object]] = {}
    if isinstance(value, dict):
        for name, child in value.items():
            child_prefix = f"{prefix}.{name}" if prefix else name
            for column, values in _flatten_json(child, child_prefix).items():
                result.setdefault(column, []).extend(values)
    elif isinstance(value, list):
        list_prefix = f"{prefix}[]"
        for child in value:
            for column, values in _flatten_json(child, list_prefix).items():
                result.setdefault(column, []).extend(values)
    elif prefix:
        result[prefix] = [value]
    return result


def _csv_records(path: Path) -> Iterator[dict[str, list[object]]]:
    with path.open(encoding="utf-8-sig", newline="") as stream:
        reader = csv.reader(stream)
        try:
            headers = next(reader)
        except StopIteration:
            return
        # Preserve duplicate and blank headers rather than silently losing columns.
        occurrences: dict[str, int] = {}
        names: list[str] = []
        for position, header in enumerate(headers, 1):
            base = header.strip() or f"<column {position}>"
            occurrences[base] = occurrences.get(base, 0) + 1
            suffix = f" [{occurrences[base]}]" if occurrences[base] > 1 else ""
            names.append(f"{base}{suffix}")
        for row in reader:
            padded = [*row, *("" for _ in range(max(0, len(names) - len(row))))]
            yield {name: [padded[index]] for index, name in enumerate(names)}


def _json_records(path: Path) -> Iterator[dict[str, list[object]]]:
    with path.open(encoding="utf-8-sig") as stream:
        data = json.load(stream)
    records = data if isinstance(data, list) else [data]
    for record in records:
        if isinstance(record, dict):
            yield _flatten_json(record)
        else:
            yield {"<value>": [record]}


def profile_export(root: Path) -> list[DatasetProfile]:
    """Profile supported files under *root*; silently ignore other file types."""
    root = root.expanduser().resolve()
    if not root.is_dir():
        raise ValueError(f"not a directory: {root}")

    profiles: dict[str, DatasetProfile] = {}
    for path in sorted(item for item in root.rglob("*") if item.is_file()):
        relative = path.relative_to(root).as_posix()
        suffix = path.suffix.casefold()
        if suffix not in {".csv", ".json"}:
            continue
        normalized = normalize_sharded_path(root, path)
        profile = profiles.setdefault(normalized, DatasetProfile(normalized))
        profile.physical_files.add(relative)
        records = _csv_records(path) if suffix == ".csv" else _json_records(path)
        try:
            for record in records:
                profile.add_record(record)
        except (OSError, UnicodeError, csv.Error, json.JSONDecodeError) as exc:
            raise ValueError(f"cannot read {relative}: {exc}") from exc
    return sorted(profiles.values(), key=lambda item: item.path.casefold())


def _display_value(value: str) -> str:
    """Render arbitrary text safely inside a Markdown table cell."""
    if len(value) > EXAMPLE_LENGTH_LIMIT:
        value = f"{value[:EXAMPLE_LENGTH_LIMIT]}…"
    return json.dumps(value, ensure_ascii=False).replace("|", "\\|")


def _value_summary(column: ColumnProfile) -> str:
    values = sorted(column.values, key=str.casefold)
    if len(values) <= LOW_CARDINALITY_LIMIT:
        return "all: " + ", ".join(_display_value(value) for value in values)
    return "examples: " + ", ".join(
        _display_value(value) for value in column.examples
    )


def _write_toc_group(
    title: str,
    indexed_paths: Iterable[tuple[int, str]],
    stream: TextIO,
) -> None:
    """Write one thematic group of links in the report table of contents."""
    print(f"### {title}", file=stream)
    print(file=stream)
    for number, display_path in indexed_paths:
        print(f"- [`{display_path}`](#dataset-{number})", file=stream)
    print(file=stream)


def write_markdown(profiles: Iterable[DatasetProfile], stream: TextIO) -> None:
    """Write a Markdown exploration report to a text stream."""
    output = stream
    profile_list = list(profiles)
    display_paths: list[str] = []
    for profile in profile_list:
        normalized = Path(profile.path)
        shard_glob = (normalized.parent / f"*{normalized.suffix}").as_posix()
        display_paths.append(
            shard_glob if len(profile.physical_files) > 1 else profile.path
        )

    print("# Kindle export data profile", file=output)
    print(file=output)
    print(
        "Empty records means records with no non-empty value for that column. "
        "JSON arrays can contribute multiple values to one record.",
        file=output,
    )
    print(file=output)
    print("## Table of contents", file=output)
    print(file=output)
    indexed_paths = list(enumerate(display_paths, 1))
    _write_toc_group(
        "Book",
        (
            item
            for item in indexed_paths
            if any(marker in item[1] for marker in BOOK_PATH_MARKERS)
        ),
        output,
    )
    _write_toc_group(
        "Acquisition",
        (
            item
            for item in indexed_paths
            if any(marker in item[1] for marker in ACQUISITION_PATH_MARKERS)
        ),
        output,
    )
    _write_toc_group(
        "Reading",
        (
            item
            for item in indexed_paths
            if any(marker in item[1] for marker in READING_PATH_MARKERS)
        ),
        output,
    )
    _write_toc_group("All files", indexed_paths, output)

    for number, (profile, display_path) in enumerate(
        zip(profile_list, display_paths, strict=True), 1
    ):
        normalized = Path(profile.path)
        shard_glob = (normalized.parent / f"*{normalized.suffix}").as_posix()
        print(file=output)
        print(f'<a id="dataset-{number}"></a>', file=output)
        print(file=output)
        print(f"## `{display_path}`", file=output)
        print(file=output)
        print(f"- Physical files: {len(profile.physical_files)}", file=output)
        print(f"- Records: {profile.record_count}", file=output)
        if len(profile.physical_files) > 1:
            print(f"- Shards: `{shard_glob}`", file=output)
        print(file=output)
        print(
            "| Column | Values | Unique values | Populated records | Empty records | Key-like | Values / examples |",
            file=output,
        )
        print("|---|---:|---:|---:|---:|:---:|---|", file=output)
        for name, column in sorted(
            profile.columns.items(), key=lambda item: item[0].casefold()
        ):
            empty = profile.record_count - column.populated_records
            escaped_name = name.replace("|", "\\|")
            key_like = (
                "yes"
                if empty == 0
                and column.value_count == profile.record_count
                and len(column.values) == profile.record_count
                else ""
            )
            print(
                f"| `{escaped_name}` | {column.value_count} | "
                f"{len(column.values)} | {column.populated_records} | {empty} | "
                f"{key_like} | {_value_summary(column)} |",
                file=output,
            )


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("export_directory", type=Path)
    parser.add_argument(
        "-o", "--output", type=Path, help="Write Markdown to this file instead of stdout"
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        profiles = profile_export(args.export_directory)
        if args.output:
            args.output.parent.mkdir(parents=True, exist_ok=True)
            with args.output.open("w", encoding="utf-8") as stream:
                write_markdown(profiles, stream)
        else:
            write_markdown(profiles, sys.stdout)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
