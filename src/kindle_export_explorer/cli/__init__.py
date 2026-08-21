"""Unified command-line interface for Kindle export exploration."""

import click

from .books import books
from .overview import overview
from .reading import reading


@click.group(context_settings={"help_option_names": ["-h", "--help"]})
def main() -> None:
    """Explore and reconstruct an Amazon Kindle data export."""


main.add_command(books)
main.add_command(overview)
main.add_command(reading)


__all__ = ["books", "main", "overview", "reading"]
