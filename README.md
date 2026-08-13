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

Use `--raw` to append every connected book/source field available for the selected
books:

```console
uv run kindle-books --raw /path/to/Kindle > books-raw.tsv
uv run kindle-books --raw --source all --show-samples --show-default /path/to/Kindle > everything-raw.tsv
```

Use `--jsonl` (or its `--json` alias) to write one JSON object per line instead of
TSV. JSON output uses arrays for authors and genres and a boolean for `is_sample`:

```console
uv run kindle-books --jsonl /path/to/Kindle > books.jsonl
uv run kindle-books --json --raw /path/to/Kindle > books-raw.jsonl
```

Select one or more records with a comma-separated, case-insensitive list of ASINs or
personal-document IDs. `--include` works with both TSV and JSON Lines output and can
mix both identifier types. Explicit IDs override the source, sample, and default-
content filters, so requested print books, samples, dictionaries, and user guides are
returned without their corresponding `--source` or `--show-*` options:

```console
uv run kindle-books --include B00B7NPRY8,B008T9L6AM /path/to/Kindle
uv run kindle-books --jsonl --include B00B7NPRY8,DOC-123 /path/to/Kindle
```

Exclude ASINs or document IDs with the equivalent `--exclude` filter. Unknown or
already-filtered identifiers have no effect. When combined, inclusion overrides the
category filters first, then exclusion wins:

```console
uv run kindle-books --exclude B008T9L6AM,DOC-123 /path/to/Kindle
uv run kindle-books --jsonl --include B00B7NPRY8,DOC-123 --exclude DOC-123 /path/to/Kindle
```

In raw mode, every header states its provenance. Only values actually computed by
the tool use `synthetic->{field_name}`; currently these are `source` and `is_sample`.
Fields read from the export use
`{export-relative/path/to/file}->{field_name}`. JSON subfields retain their object
path, for example:

```text
synthetic->is_sample
Digital.Content.Ownership/shard.json->resource.ASIN
Digital.Content.Ownership/shard.json->resource.Product Name
Digital.Content.Ownership/shard.json->rights.acquiredDate
Kindle.UnifiedLibraryIndex/datasets/.../CustomerRelationshipIndex.1.1.csv->Our Price
```

True numbered shard groups are normalized to `{directory}/shard.{extension}`, so
packaging numbers do not create hundreds of sparse columns. Singleton numbered files
retain their exact names. When several records in one normalized source provide
different values, the values are joined with `; `. Raw identifiers, titles, authors,
genres, and series metadata retain their actual source paths instead of being
relabeled as synthetic. Acquisition dates remain separate as ownership
`rights.acquiredDate`, ULI `Relationship Creation Date`, and personal-document
`EntryCreationDate`. See the ER diagram for canonical field precedence. Reading,
annotation, synchronization, content-update, timestamp, and device activity
tables are not used.

The export directory is the directory containing folders such as
`Digital.Content.Ownership` and `Kindle.UnifiedLibraryIndex`. The separate
`Kindle.FileDescriptions.csv` file is not required.

Use `uv run kindle-books --help` for CLI help.

See [`docs/ER_DIAGRAM.md`](docs/ER_DIAGRAM.md) for the entity–relationship diagram,
join keys, provenance rules, and currently unconnected entities.

## Develop

```console
uv run pytest
uv run pyright
```
