# kindle-export-reassembly

Reconstruct a deduplicated, tab-separated book list from an Amazon Kindle data export.
The output contains book metadata only: identifiers, title, authors, genres, and
series. Reading sessions, progress, annotations, acquisition dates, and other user
activity are not emitted.

## Run

```console
uv run kindle-books /path/to/Kindle > books.tsv
```

Only Kindle books are emitted by default. The Unified Library Index also contains
print purchases; select them with `--source print`, or select both sources with
`--source all`:

```console
uv run kindle-books --source print /path/to/Kindle > print-books.tsv
uv run kindle-books --source all /path/to/Kindle > kindle-and-print-books.tsv
```

Source detection uses Kindle ownership records rather than the shape of the ASIN, so
print books with a `B`-prefixed ASIN are still classified as print. The TSV includes a
`source` column containing `kindle` or `print`.

Book samples, Kindle-supplied dictionaries, and Kindle user guides are also excluded
by default. Include either group with `--show-samples` or `--show-default`:

```console
uv run kindle-books --show-samples /path/to/Kindle > books-with-samples.tsv
uv run kindle-books --show-default /path/to/Kindle > books-with-defaults.tsv
uv run kindle-books --source all --show-samples --show-default /path/to/Kindle > all-books.tsv
```

The TSV includes an `is_sample` column. Purchased books are retained even when their
titles contain words such as “dictionary” or “manual”; default-content filtering uses
Amazon's ownership origin metadata rather than title matching.

The export directory is the directory containing folders such as
`Digital.Content.Ownership` and `Kindle.UnifiedLibraryIndex`. The separate
`Kindle.FileDescriptions.csv` file is not required.

Use `uv run kindle-books --help` for CLI help.

## Develop

```console
uv run pytest
```
