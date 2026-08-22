#!/usr/bin/env python3
"""Show rolling ASIN state implied by Whispersync ``kindle.*_read`` rows."""

from __future__ import annotations

import argparse
import csv
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Iterator

from kindle_export_explorer.books import ExportFiles, ExportPath, clean
from kindle_export_explorer.paths import resolve_export_path


_ANNOTATION_TYPES = (
    "kindle.most_recent_read",
    "kindle.last_read",
)


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "export_path",
        nargs="?",
        type=Path,
        help="Kindle directory or ZIP archive (or set KINDLE_EXPORT_PATH)",
    )
    parser.add_argument(
        "--active-only",
        action="store_true",
        help="Exclude rows exported as deleted tombstones.",
    )
    args = parser.parse_args(argv)

    try:
        root = resolve_export_path(args.export_path)
    except ValueError as exc:
        parser.error(str(exc))
    paths = ExportFiles(root).named("whispersync")
    if not paths:
        parser.error(f"no Whispersync CSV found in {root}")

    updates: dict[datetime, dict[str, set[str]]] = defaultdict(
        lambda: defaultdict(set)
    )
    for row in _csv_rows(paths):
        annotation_type = clean(row.get("Annotation Type"))
        asin = clean(row.get("ASIN"))
        timestamp = _parse_timestamp(row.get("Customer modified date on device"))
        if annotation_type not in _ANNOTATION_TYPES or not asin or timestamp is None:
            continue
        if args.active_only and clean(row.get("Is Deleted")).casefold() == "yes":
            continue
        updates[timestamp][annotation_type].add(asin)

    writer = csv.writer(sys.stdout, dialect="excel-tab", lineterminator="\n")
    writer.writerow(
        [
            "ts",
            "most_recent_read",
            "last_read",
        ]
    )
    state = {annotation_type: "" for annotation_type in _ANNOTATION_TYPES}
    for timestamp in sorted(updates):
        previous_state = state.copy()
        for annotation_type, asins in updates[timestamp].items():
            # Multiple ASINs occasionally share the same second. Preserve the
            # ambiguity rather than imposing an arbitrary row order.
            state[annotation_type] = ",".join(sorted(asins))
        writer.writerow(
            [
                _format_timestamp(timestamp),
                _marked_state(
                    state["kindle.most_recent_read"],
                    previous_state["kindle.most_recent_read"],
                ),
                _marked_state(
                    state["kindle.last_read"],
                    previous_state["kindle.last_read"],
                ),
            ]
        )


def _csv_rows(paths: list[ExportPath]) -> Iterator[dict[str, str]]:
    for path in paths:
        with path.open(encoding="utf-8-sig", newline="") as stream:
            yield from csv.DictReader(stream)


def _parse_timestamp(value: object) -> datetime | None:
    timestamp = clean(value)
    if not timestamp:
        return None
    try:
        return datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
    except ValueError:
        return None


def _marked_state(current: str, previous: str) -> str:
    if not current:
        return ""
    marker = "|" if current == previous else "-"
    return ",".join(f"{asin}{marker}" for asin in current.split(","))


def _format_timestamp(value: datetime) -> str:
    return value.isoformat().replace("+00:00", "Z")


if __name__ == "__main__":
    main()
