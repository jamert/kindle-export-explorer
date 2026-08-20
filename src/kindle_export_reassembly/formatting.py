"""Shared output formatting helpers."""

from datetime import datetime


def format_datetime(value: datetime) -> str:
    """Format a datetime as ISO 8601, using ``Z`` for UTC."""
    result = value.isoformat()
    return f"{result[:-6]}Z" if result.endswith("+00:00") else result
