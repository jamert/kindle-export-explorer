# kindle-export-reassembly

Reconstruct canonical book records from an Amazon Kindle data export. Source rows are
joined first, then resolved into the `BookCanonical` schema. Acquisition and reading
records are available through separate Python APIs.

## Run

```console
uv run kindle-books /path/to/Kindle > books.tsv
```

The default TSV fields are:

```text
key  asin  document_id  title  author  ownership_digital  ownership_print
```

`ownership_digital` is one of `unknown`, `default`, `kindle_sample`, `kindle_ebook`,
or `personal_document`. `ownership_print` is true only for a Unified Library Index
ownership record without digital-ownership evidence.

Use `--extra` to add series title, series ASIN, series position, genres, and
marketplace-derived links for the book and its series:

```console
uv run kindle-books --extra /path/to/Kindle > books-extra.tsv
```

Use `--jsonl` (or `--json`) for one JSON object per line. JSON preserves the nested
`author` and `series` structures and arrays such as genres. Books without series
metadata have `"series": null` when `--extra` is enabled:

```console
uv run kindle-books --jsonl --extra /path/to/Kindle > books.jsonl
```

## Keys and filtering

Canonical keys identify Amazon books by ASIN and personal documents by document ID:

```text
asin:<ASIN>
document:<DocumentId>
```

A Kindle sample and full ebook with the same ASIN are merged. The merged record has
`ownership_digital=kindle_ebook`; a sample-only record remains `kindle_sample`.

Samples, Kindle defaults, and Amazon's Cloud Drive notice are hidden by default.
Print-only records are available with `--source print`; use `--source all` for every
ownership type:

```console
uv run kindle-books --show-samples /path/to/Kindle
uv run kindle-books --show-default /path/to/Kindle
uv run kindle-books --source print /path/to/Kindle
uv run kindle-books --source all --show-samples --show-default /path/to/Kindle
```

Select comma-separated keys, ASINs, or document IDs with `--include`. Explicit
inclusion overrides source, sample, and default-content filters. `--exclude` always
wins:

```console
uv run kindle-books --include B00B7NPRY8,DOC-123 /path/to/Kindle
uv run kindle-books --include B00B7NPRY8,DOC-123 --exclude DOC-123 /path/to/Kindle
```

When one canonical key has conflicting marketplaces, the program warns on stderr and
prefers the `.com` marketplace. Without `.com`, it chooses the first marketplace by
sorted domain.

## Acquisition timelines

The Python API also reconstructs acquisition events separately from intrinsic book
metadata:

```python
from kindle_export_reassembly import reconstruct_acquisitions

acquisitions = reconstruct_acquisitions(export_directory)
```

Each `BookAcquisition` uses the same `CanonicalKey` as `BookCanonical` and contains a
chronological event list. Kindle `rights.acquiredDate` supplies sample, purchase, and
default-content events; ULI `Relationship Creation Date` supplies the best available
print-acquisition timestamp; personal documents use `EntryCreationDate`. Content
consumption/download records are deliberately ignored.

Add the derived timestamps to TSV or JSONL book output with `--acquisition`:

```console
uv run kindle-books --acquisition /path/to/Kindle > books-with-acquisition.tsv
```

The additional fields are `acquired_sample` and `acquired_book`. Missing timestamps
are empty TSV cells or JSON `null` values.

## Reading records

The Python API collects source-specific reading records by canonical key without
attempting to merge them into canonical sessions:

```python
from kindle_export_reassembly import reconstruct_reading

reading = reconstruct_reading(export_directory)
```

Each `BookReading` has separate lists for device sessions, Reading Insights sessions,
Whispersync records, reading-action containers and widgets, automatic mark-as-read
records, and title-completion records. Sample/full provenance remains on each source
record. Records without an ASIN or an exact personal-document ID are omitted.

The export directory is the directory containing folders such as
`Digital.Content.Ownership` and `Kindle.UnifiedLibraryIndex`. The separate
`Kindle.FileDescriptions.csv` file is not required.

Use `uv run kindle-books --help` for CLI help.

See [`docs/ER_DIAGRAM.md`](docs/ER_DIAGRAM.md) for source relationships and join
rules.

## Explore an export

Generate a Markdown inventory of every CSV/JSON file and column, including record
counts, unique and empty values, low-cardinality domains, examples, and key-like
columns. True file shards and versioned dataset partitions are profiled together:

```console
uv run python scripts/explore_export.py /path/to/Kindle -o data-profile.md
```

## Develop

```console
uv run pytest
uv run pyright
```
