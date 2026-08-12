# kindle-export-reassembly

Reconstruct a deduplicated, tab-separated book list from an Amazon Kindle data export.
The output contains book metadata only: identifiers, title, authors, genres, and
series. Reading sessions, progress, annotations, acquisition dates, and other user
activity are not emitted.

## Run

```console
uv run kindle-books /path/to/Kindle > books.tsv
```

Book samples, Kindle-supplied dictionaries, and Kindle user guides are excluded by
default. Include either group with `--show-samples` or `--show-default`:

```console
uv run kindle-books --show-samples /path/to/Kindle > books-with-samples.tsv
uv run kindle-books --show-default /path/to/Kindle > books-with-defaults.tsv
uv run kindle-books --show-samples --show-default /path/to/Kindle > all-books.tsv
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
