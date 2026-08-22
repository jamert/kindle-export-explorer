# kindle-export-explorer

Utility takes data from Amazon's Kindle Data Request and produces:
1. A list of books (primarily Kindle ones) that you have from Amazon (curiously, it includes print books, too)
2. Reconstruction of reading sessions (in progress).

Kindle export data are not documented precisely and 

> [!WARNING]
> The utility is early work in progress.
> Code is heavily AI-generated, without much style enforcement at the moment.
>
> I have two goals for this project:
> 1. To produce useful utility for personal use
> 2. To try out some experimental practices related to data processing and agentic coding

## Plans

- Detailed reconstructed reading sessions data
- Cross-checks between different reading sessions sources
- Reasonably supported claims that the book was finished
- Enrich books with external information (ISBN, possibly page count)
- Extra automatic verification practices: fuzzing, source-result provenance, etc.

## Run

The unified CLI provides `books`, `overview`, and `reading` subcommands. Every
command accepts either the original Kindle ZIP archive or an unpacked directory;
the results are identical and the archive is read directly without extraction:

```console
uv run kindle-export-explorer books /path/to/Kindle.zip > books.tsv
```

The default TSV fields are:

```text
key  asin  document_id  title  author  ownership_digital  ownership_print
```

`ownership_digital` is one of `unknown`, `default`, `kindle_sample`, `kindle_ebook`,
or `personal_document`. `ownership_print` is true only for a Unified Library Index
ownership record without digital-ownership evidence.

For a combined Kindle-only overview—including samples but excluding print books and
Kindle defaults—use:

```console
uv run kindle-export-explorer overview /path/to/Kindle > kindle-overview.tsv
```

The overview adds sample/book acquisition timestamps. Device-session summary fields
use the `reading_ds_` prefix (`reading_ds_start`, `reading_ds_end`, and
`reading_ds_total_reading_humanized`). Whispersync summary fields use `reading_ws_`
(`reading_ws_start`, `reading_ws_end`, and `reading_ws_dates_unique`). It is ordered by
full-book acquisition date, using the sample acquisition date when no full-book
acquisition exists. It supports
`--include`, `--exclude`, and `--json`.

Use `--extra` to add series title, series ASIN, series position, genres, and
marketplace-derived links for the book and its series:

```console
uv run kindle-export-explorer books --extra /path/to/Kindle > books-extra.tsv
```

Use `--jsonl` (or `--json`) for one JSON object per line. JSON preserves the nested
`author` and `series` structures and arrays such as genres. Books without series
metadata have `"series": null` when `--extra` is enabled:

```console
uv run kindle-export-explorer books --jsonl --extra /path/to/Kindle > books.jsonl
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
uv run kindle-export-explorer books --show-samples /path/to/Kindle
uv run kindle-export-explorer books --show-default /path/to/Kindle
uv run kindle-export-explorer books --source print /path/to/Kindle
uv run kindle-export-explorer books --source all --show-samples --show-default /path/to/Kindle
```

Select comma-separated keys, ASINs, or document IDs with `--include`. Explicit
inclusion overrides source, sample, and default-content filters. `--exclude` always
wins:

```console
uv run kindle-export-explorer books --include B00B7NPRY8,DOC-123 /path/to/Kindle
uv run kindle-export-explorer books --include B00B7NPRY8,DOC-123 --exclude DOC-123 /path/to/Kindle
```

When one canonical key has conflicting marketplaces, the program warns on stderr and
prefers the `.com` marketplace. Without `.com`, it chooses the first marketplace by
sorted domain.

## Acquisition timelines

The Python API also reconstructs acquisition events separately from intrinsic book
metadata:

```python
from kindle_export_explorer import reconstruct_acquisitions

acquisitions = reconstruct_acquisitions(export_directory)
```

Each `BookAcquisition` uses the same `CanonicalKey` as `BookCanonical` and contains a
chronological event list. Kindle `rights.acquiredDate` supplies sample, purchase, and
default-content events; ULI `Relationship Creation Date` supplies the best available
print-acquisition timestamp; personal documents use `EntryCreationDate`. Content
consumption/download records are deliberately ignored.

Add the derived timestamps to TSV or JSONL book output with `--acquisition`:

```console
uv run kindle-export-explorer books --acquisition /path/to/Kindle > books-with-acquisition.tsv
```

The additional fields are `acquired_sample` and `acquired_book`. Missing timestamps
are empty TSV cells or JSON `null` values.

## Reading records

The Python API collects source-specific reading records by canonical key without
attempting to merge them into canonical sessions:

```python
from kindle_export_explorer import reconstruct_reading

reading = reconstruct_reading(export_directory)
```

Each `BookReading` has separate lists for device sessions, Reading Insights sessions,
Whispersync reading-position records, reading-action containers, automatic
mark-as-read records, and title-completion records. Sample/full provenance remains on each source
record. Records without an ASIN or an exact personal-document ID are omitted.
Whispersync retains only `kindle.last_read` and `kindle.most_recent_read`.
`kindle.continuous_read` is excluded because every one of its ASIN and
customer-modified timestamp updates duplicates `kindle.most_recent_read` in the
profiled export, so it contributes no additional observable reading evidence.

Inspect one book's records as JSON with its canonical key. The command joins the canonical title from the ownership metadata and includes a
`device_sessions_summary` with the minimum start, maximum end, summed non-zero reading
milliseconds, a minute-resolution humanized duration such as `5h14m`, summed page
flips, and a count of sessions with non-zero reading time. The summary is `null` when
there are no device-session records. It also includes `whispersync_record_summary`, based only on `kindle.most_recent_read`, with
the earliest creation timestamp, latest customer-modified timestamp, and number of
distinct calendar dates represented by the earliest creation plus all customer-modified
timestamps:

```console
uv run kindle-export-explorer reading asin:B004PYDAPE /path/to/Kindle
uv run kindle-export-explorer reading document:3TH4XYQKJZXM3ZEXIR6IZODX6IMC4YAD /path/to/Kindle
```

The export path can be the ZIP archive or the unpacked directory containing
folders such as `Digital.Content.Ownership` and `Kindle.UnifiedLibraryIndex`.
The separate `Kindle.FileDescriptions.csv` file is not required.

Use `uv run kindle-export-explorer --help` for CLI help.

See [`docs/ER_DIAGRAM.md`](docs/ER_DIAGRAM.md) for source relationships and join
rules. [`docs/WHISPERSYNC_RESEARCH.md`](docs/WHISPERSYNC_RESEARCH.md) records the
empirical interpretation of Whispersync reading state.

## Explore an export

Generate a Markdown inventory of every CSV/JSON file and column, including record
counts, unique and empty values, low-cardinality domains, examples, and key-like
columns. True file shards and versioned dataset partitions are profiled together:

```console
uv run python scripts/explore_export.py /path/to/Kindle.zip -o data-profile.md
```

## Develop

```console
uv run pytest
uv run pyright
```
