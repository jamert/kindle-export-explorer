# Kindle export reconstruction model

The application keeps ownership-specific source records separate until all relevant
sources have been joined. `CanonicalizationService` then converts each source type to
`BookCanonical`.

```mermaid
flowchart LR
    O[Digital ownership] --> K[KindleBookRecord]
    U[Unified Library Index] --> K
    U --> P[PrintBookRecord]
    D[Personal-document metadata] --> D2[DocumentRecord]
    S[Series metadata] --> K
    S --> P
    A[Author and genre relations] --> K
    A --> P
    K --> C[CanonicalizationService]
    P --> C
    D2 --> C
    C --> B[BookCanonical]
    B --> F[CLI filtering and output]
```

## Canonical schema

`BookCanonical` contains:

- `key: CanonicalKey`
- `title: str`
- `authors: Authors`
- `ownership_digital: DigitalOwnership`
- `ownership_print: bool`
- `series: Series | None`, where a present series has a required title, nullable ASIN,
  and nullable integer position
- `genres: list[str]`
- `marketplace: str`
- computed `link: str | None` (`https://{marketplace}/dp/{asin}`)
- computed `series_link: str | None` (`https://{marketplace}/dp/{series.asin}`)

The default CLI output contains the formatted key, its nullable ASIN and document-ID
components, title, author, digital ownership, and print ownership. Sample status is
represented by `ownership_digital`, not by the canonical key or a separate output
field. `--extra` adds series, genres, and computed book and series links. Marketplace
is retained internally only to construct those links. JSONL
emits `series: null` when no series metadata exists; nullable series components remain
empty TSV cells.

### Canonical key

`CanonicalKey` contains either an Amazon ASIN or a personal-document ID. Its string
representation is:

```text
asin:<ASIN>
document:<DocumentId>
```

### Ownership

`DigitalOwnership` has these values:

- `unknown`: no digital-ownership evidence; normally a print-only ULI record
- `default`: content supplied by Amazon, including dictionaries, user guides, and the
  Cloud Drive notice
- `kindle_sample`
- `kindle_ebook`
- `personal_document`

`ownership_print` is true only when an owned Unified Library Index item has no digital
ownership evidence. ULI also repeats many Kindle purchases, so its presence cannot by
itself prove ownership of a separate physical edition. The export cannot reliably
represent simultaneous print and Kindle ownership under one ASIN.

## Canonical precedence

- **Title:** ULI `Sortable Title`, then the selected ordinary title. Ordinary-title
  precedence remains ULI Product Name, Saga item product name, BookRelation Product
  Name, Digital Ownership Product Name, or personal-document Title as applicable.
- **Authors:** all `Author Name` relation values, falling back to ULI `Sortable Author
  Name` only when no author-name relationships exist, then `DocumentProvider` for
  personal documents. The sortable value never replaces or supplements ordinary
  names because it may contain only the primary author and uses different formatting.
  `Authors.names` and `Authors.asins` preserve the available author names and Amazon
  author-page ASINs as separate lists. They are deliberately unpaired because some
  authors have no Amazon page and the source provides no name-to-ID relationship.
- **Series:** Saga provides `series-product-name`, `series-ASIN`, and item position as
  separate fields. ULI commonly encodes the title and ASIN together as
  `<series title> <series ASIN>`; this value is split using a strict ASIN suffix before
  canonicalization. A ULI series value without that suffix is retained with a null
  series ASIN. The export has no explicit sortable series title.
- **Genres:** distinct CustomerGenres values.
- **Marketplace:** ULI Marketplace. Conflicts produce a warning on stderr. An
  `amazon.com` domain wins; otherwise the first value by sorted domain wins.

## Source joins

Kindle ebook and sample rows are accumulated as separate `KindleBookRecord` objects,
deduplicated by ASIN and variant. `CanonicalizationService.convert_kindle(*records)`
merges each ASIN group into one canonical output. Full-ebook ownership takes precedence
over sample ownership. Print records are deduplicated by ASIN, while personal
documents are deduplicated by `DocumentId`. Saga item identifiers with the
`urn:collection:1:asin-` prefix are normalized before joining.

No joins use title, author name, author ID, order ID, filename, or fuzzy matching.
Author-ID rows are retained in the joined source data but do not identify a particular
name when a book has multiple authors.

## Classification evidence

- Digital Content Ownership proves Kindle digital ownership.
- A ULI `Sample Owner` relation proves sample ownership.
- Personal-document metadata proves personal-document ownership.
- ULI `Item Owner` without digital evidence produces `unknown` digital ownership and
  true print ownership.
- Ownership origins `KindleDictionary` and `KindleUserGuide` identify defaults.
- The Cloud Drive notice is identified by provider and filename rather than its
  account-specific document ID.

## Acquisition timeline

Acquisition reconstruction is a separate pipeline in `acquisitions.py`:

```text
KindleAcquisitionRecord ─┐
PrintAcquisitionRecord ──┼─> AcquisitionCanonicalizationService ─> BookAcquisition
DocumentAcquisitionRecord┘
```

`BookAcquisition` shares `CanonicalKey` with `BookCanonical` and contains chronological
`AcquisitionEvent` values:

- `sample_acquired` from Digital Ownership `rights.acquiredDate` with Sample origin
- `kindle_purchased` from `rights.acquiredDate` with Purchase origin
- `kindle_default_acquired` for dictionary and user-guide origins
- `print_acquired` from ULI `Relationship Creation Date`
- `document_created` from DocumentMetadata `EntryCreationDate`

The print timestamp indicates when ULI ownership was recorded; it is not guaranteed to
be the retail transaction time. CustomerOrders has order identifiers but no timestamp,
so it is not used. Digital Ownership content-consumption dates describe downloads and
are also excluded from acquisition events. `BookAcquisition.acquired_sample` and
`acquired_book` expose the first corresponding events; `--acquisition` joins these
values to CLI book output by `CanonicalKey`.

## Reading record collection

Reading reconstruction remains source-preserving in `reading.py`:

```text
DeviceReadingSessionRecord ─────┐
ReadingInsightsSessionRecord ───┤
WhispersyncRecord ──────────────┤
ReadingActionContainerRecord ───┼─> BookReading (grouped by CanonicalKey)
AutoMarkAsReadRecord ───────────┤
TitleCompletionRecord ──────────┘
```

`BookReading` has one list per source record type. It does not yet expose a canonical
activity/session schema, match duplicate Device and Reading Insights sessions, join
adjacent sessions, or select a preferred duration. Source content types retain
sample/full provenance. Only Whispersync `kindle.last_read` and
`kindle.most_recent_read` records are retained. `kindle.continuous_read` is omitted
because its ASIN and customer-modified timestamp always duplicate
`kindle.most_recent_read` in this export. Annotations such as highlights, notes, and
bookmarks are also omitted. See [WHISPERSYNC_RESEARCH.md](WHISPERSYNC_RESEARCH.md) for
the supporting correlations and interpretation. Whispersync PDOC rows join through
exact document IDs; rows without an ASIN or exact personal-document ID are omitted. Reading Insights day units
are also omitted because they have no book identifier.

## Deliberately separate activity

`BookReading`, acquisitions, content updates, and device activity are not joined into
`BookCanonical`. They reference logical content but are not intrinsic book metadata.
