# Whispersync reading-state research notes

These notes document empirical observations from one Amazon Kindle export. They are
inferences from correlations between Whispersync rows and timed reading sessions, not
official Amazon field definitions.

## Retained record types

The raw `Digital.Content.Whispersync/whispersync.csv` contains bookmarks, highlights,
notes, and several reading-state annotation types. Reading reconstruction retains only:

- `kindle.most_recent_read`
- `kindle.last_read`

`kindle.continuous_read` is excluded. The export contains 290 `most_recent_read` and
290 `continuous_read` rows. Of those, 286 pairs are identical in every exported field
except annotation type. The remaining four pairs differ only by one-second creation or
server-update metadata. Projected onto ASIN and customer-modified timestamp, all 263
update timestamps are identical between the two types. The export therefore exposes
no additional information in `continuous_read`.

## Three timestamps

Whispersync rows expose three timestamps:

- `Creation Date` appears to identify creation of a synchronization-state object. It
  is not reliably a reading-session start or a book's first-read date.
- `Customer modified date on device` is the best available timestamp for a reading
  position changing on a client.
- `LastUpdatedDate` is a server synchronization timestamp, not reading time.

A book can have several rows of the same annotation type with different creation
dates. Before `continuous_read` was excluded, grouping by canonical key, content type,
and creation date produced 293 state generations. Of these, 261 contained a complete
`last_read`/`most_recent_read`/`continuous_read` trio. This suggests that creation date
identifies a generation of related synchronization objects, possibly recreated after
a download, migration, reinstall, format change, or use by another client.

Sample (`EBSP`) and full-ebook (`EBOK`) state must remain distinct even though they
share a canonical ASIN.

## Correlation with reading sessions

The following rolling Whispersync state occurred in August 2025:

```text
ts                    most_recent_read  last_read
2025-08-19T17:25:34Z  B076Q58RDM       B076Q58RDM
2025-08-19T17:33:15Z  B076Q58RDM       B09KS8RZHF
2025-08-20T18:49:18Z  B09KS8RZHF       B09KS8RZHF
2025-08-20T18:51:50Z  B0092EE3EY       B09KS8RZHF
2025-08-23T09:22:14Z  B0092EE3EY       B09KS8RZHF
2025-08-23T10:30:14Z  B0CF2CWTY7       B09KS8RZHF
2025-08-23T10:58:58Z  B0CF2CWTY7       B09KS8RZHF
```

The corresponding `Kindle.Devices.ReadingSession` records are:

| Whispersync update | Reading-session evidence |
|---|---|
| `2025-08-19 17:25:34` MR `B076Q58RDM` | Six-second sample revisit, one page flip |
| `2025-08-19 17:33:15` LR `B09KS8RZHF` | Near the end of a 7m34s sample session with 52 flips |
| `2025-08-20 18:49:18` MR `B09KS8RZHF` | Six-second sample revisit, one flip |
| `2025-08-20 18:51:50` MR `B0092EE3EY` | Near the end of a two-minute session with 16 flips |
| `2025-08-23 09:22:14` MR `B0092EE3EY` | Five-second revisit, one flip |
| `2025-08-23 10:30:14` MR `B0CF2CWTY7` | Near the end of an 8m18s session with 14 flips |
| `2025-08-23 10:58:58` MR `B0CF2CWTY7` | Near the end of an 18m25s session with 41 flips |

Earlier history provides additional context:

- `B076Q58RDM` had a substantial sample session on August 18. Its `last_read` changed
  near the end. A six-second revisit on August 19 changed only `most_recent_read`.
- `B09KS8RZHF` had a substantial first sample session on August 19. Its `last_read`
  changed near the end. A six-second revisit the next day changed only
  `most_recent_read`.
- `B0092EE3EY` had reading state dating to 2016. Its 2025 sessions changed
  `most_recent_read`, while its own `last_read` state remained in 2016.
- `B0CF2CWTY7` was read extensively in 2024. Its 2025 rereading sessions changed
  `most_recent_read`, while its own `last_read` remained at June 2024.

## Working interpretation

The evidence supports this provisional interpretation:

- **`most_recent_read`** tracks the current or most recently reported reading
  position. It changes during ordinary reading and rereading, including very short
  one-page revisits.
- **`last_read`** behaves like a progress high-water marker, possibly the furthest
  position reached. It appears to change when reading advances beyond previously
  recorded progress, but not while rereading earlier material.

In a global rolling-state display, this can be summarized as:

```text
most_recent_read = the book whose current-position state changed most recently
last_read        = the book whose furthest-progress state changed most recently
```

The second description remains a hypothesis because the export omits the actual
position payload.

## Limitations

- Neither annotation type provides session duration, page count, page number, or
  progress percentage.
- Customer-modified timestamps can occur at session start, during a session, or shortly
  before its end. They are synchronization updates, not exact session boundaries.
- Page flips are not equivalent to pages read.
- Forty-seven raw `kindle.*_read` rows are marked deleted. These are historical
  synchronization tombstones and should not be interpreted as current state.
- Sample and full-book records can share an ASIN but describe different reading state.
- Findings are based on one export and may vary by Kindle client or export version.

## Exploration script

Generate the global rolling state with:

```console
uv run python scripts/explore_whispersync_read_state.py /path/to/Kindle
```

The script appends `|` to an ASIN unchanged from the previous row and `-` to a changed
ASIN. It includes deleted historical records by default; pass `--active-only` to omit
tombstones.
