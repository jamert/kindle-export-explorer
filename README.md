# kindle-export-reassembly

Reconstruct a deduplicated, tab-separated book list from an Amazon Kindle data export.
The output contains book metadata only: identifiers, title, authors, genres, and
series. Reading sessions, progress, annotations, acquisition dates, and other user
activity are not emitted.

## Run

```console
uv run kindle-books /path/to/Kindle > books.tsv
```

The export directory is the directory containing folders such as
`Digital.Content.Ownership` and `Kindle.UnifiedLibraryIndex`. The separate
`Kindle.FileDescriptions.csv` file is not required.

Use `uv run kindle-books --help` for CLI help.

## Develop

```console
uv run pytest
```
