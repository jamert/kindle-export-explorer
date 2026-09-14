"""Render file I/O boundary violations for pytest's terminal summary."""

from __future__ import annotations

import linecache
from collections import Counter
from collections.abc import Mapping, Sequence

from .model import ApplicationFrame, Boundary, FileIOViolation


def violation_report_lines(
    violations: Mapping[FileIOViolation, int],
    *,
    verbose: bool,
) -> list[str]:
    if verbose:
        return _verbose_report_lines(violations)

    frame_counts: Counter[Boundary] = Counter()
    for violation, count in violations.items():
        for frame in violation.frames:
            frame_counts[frame.boundary] += count
    total = sum(violations.values())
    lines = [f"{total} read events occurred outside allowed boundaries:"]
    for boundary, count in sorted(
        frame_counts.items(),
        key=lambda item: (-item[1], item[0]),
    ):
        lines.append(f"  {count:>4}  {boundary}")
    lines.append("Run pytest -v to show individual paths and application stacks.")
    return lines


def _verbose_report_lines(
    violations: Mapping[FileIOViolation, int],
) -> list[str]:
    lines: list[str] = []
    for violation, count in violations.items():
        suffix = f" ({count} occurrences)" if count > 1 else ""
        lines.append(f"read: {violation.target}{suffix}")
        lines.append("Application stack (most recent call last):")
        lines.extend(_standard_stack_lines(violation.frames))
        lines.append("  No allowed read boundary was present.")
    return lines


def _standard_stack_lines(frames: Sequence[ApplicationFrame]) -> list[str]:
    lines: list[str] = []
    for frame in frames:
        lines.append(
            f'  File "{frame.filename}", line {frame.lineno}, '
            f"in {frame.boundary.qualname}"
        )
        source = linecache.getline(frame.filename, frame.lineno).strip()
        if source:
            lines.append(f"    {source}")
    return lines
