# Fingerprints hash fixed chunks, and interrupted scans resume

Status: Accepted and implemented 2026-11-18. Builds on ADR 0106.
Amends ADRs 0058 and 0103.

A cancelled or crashed load of a large XYZ file throws away every batch it
has already parsed. On the 16 GB laptop profile that is up to 32 s of work
lost each time (ADR 0103). Two things force this:

- The cache name of a scan is a hash of the full frame index, known only at
  the end, so the scan writes under a temporary name and deletes it when
  interrupted.
- Each column's digest is one md5 fed row by row. Python cannot save an md5
  part-way, so an interrupted scan cannot continue its digests; it must start
  again from the first byte.

Spec ING-08 asks that a user-initiated reload reuse finished batches and skip
finished work.

## Decision

- **A column digest is a hash of fixed chunks.** Every column is cut into
  chunks of 16,384 rows (a row is one frame for per-frame values, one atom
  for per-atom values; the last chunk may be short). Each chunk is hashed
  on its own; the column digest is the hash of the scheme tag
  `ffast-chunks-v1-sha256-16384` followed by the chunk digests in order.
  The cut depends only on row numbers, so a plain in-memory array, a
  memory-saving column, a prediction file and a ghost model all give the
  same digest for the same values.
- **Strings and combined fingerprints use the same hash.** A string is
  hashed whole. A dataset or model fingerprint is the hash of its parts'
  digests in order, as today (ADR 0058), with the formula rules unchanged.
- **The hash is SHA-256** from the standard library. One exception: files
  written in sGDML format keep sGDML's own md5 recipe in their `md5` field,
  because sGDML checks it.
- **Full scans are named when they start.** The cache identity of a full,
  unsampled load is a hash of the reader, its cache version, the path and
  the file state (size, modification time, inode), plus the scheme tag.
  Batches are written under that name from the first window; there is no
  temporary name and no rename. Strided and sampled loads keep their
  frame-list identity, with the scheme tag added.
- **Each committed window updates a progress record.** A window is committed
  in this order: batch file written, flushed to disk (`fsync`) and renamed
  into place; the new frame starts and atom counts, and the chunk digests
  completed in this window, appended to two append-only files; then
  `<identity>.progress.json` replaced atomically. The record holds the
  scheme tag, the file state, the committed byte, frame and atom ends, the
  valid lengths of the two append-only files, the batch ranges, and the
  per-column facts the scan collects (dtype, width, missing values, element
  set, formula sample, the kept cache level and the disk allowance). A file
  `<identity>.<n>.tail.npz` holds, for each column, the rows of its one
  unfinished chunk, at most 16,384 rows, so the next window can finish that
  chunk without the source. It is named by the frame count `n` it follows and
  the record names it, so a crash between the two writes cannot pair an old
  record with a newer tail; the previous tail is deleted once the record
  that replaces it is in place. The chunk digests go to the append-only file
  as one JSON line each.
- **Reload resumes from the last committed byte.** A full load that finds a
  progress record for its identity checks the scheme tag, the file state and
  that every listed batch exists. If all match, it truncates the append-only
  files to their recorded lengths, restores the column facts and the
  unfinished chunks, and starts the frame finder at the committed byte. The
  kept cache level and the disk allowance come from the record, not from the
  disk at that moment, and the per-frame columns are read back from the
  committed batches. If anything does not match, it deletes that identity's
  record and batches and starts from the first byte, with one line in the
  log. A scan of a changed file also deletes the records, and batches, of
  earlier states of the same path (spec 4.3).
- **Interrupted scans keep committed work.** Cancel and errors delete only
  uncommitted temporary files. A finished scan writes the manifest, then
  deletes its progress record, append-only files and tail file.
- **One scan per identity.** A scan holds an exclusive lock file for its
  identity. A second server opening the same file while it is locked reads
  without writing batches. Cache folders are on local disk (spec §11a).
- **No resume without a disk cache.** When disk caching is off, nothing is
  written and an interrupted load starts again.
- **Fingerprints change once.** Every dataset, prediction and model gets a
  new fingerprint. Saved metric results and parsed caches under old
  fingerprints are no longer found; they are recalculated on use and the old
  files are left to normal eviction. Saved sessions still load their files
  by path, but settings keyed by the old fingerprint (units, cached
  prediction data of ghost models) are lost; FFAST logs one warning naming
  the session.

## Considered options

- **Chunks of frames (about 4096 frames, capped by atoms).** Chunks would line
  up with scan windows, but every place that fingerprints an array would
  need atom counts, and 4096 frames of a 10,000-atom structure is about
  3 GB of text, too much for one window.
- **Re-parse the unfinished chunk on resume instead of storing it.** No tail
  file, but resume would need a second, column-by-column catch-up path, and
  a per-frame chunk can span thousands of frames.
- **Keep md5.** Fewest changed lines. It ran at 0.58 GB/s on the laptop,
  against 2.0 GB/s for SHA-256 with CPU instructions, and md5 is too weak
  to support the strict validation of spec §4.3.
- **BLAKE3.** Cryptographic and built for chunked, parallel hashing. On the
  laptop one thread ran at 1.5 GB/s (slower than SHA-256 there), 13.4 GB/s
  on all cores. FFAST already hashes columns on up to four threads beside
  the parser, so the all-cores speed would compete with parsing. It adds a
  compiled dependency. The scheme tag names the hash, so switching later
  is one constant and one more fingerprint change.
- **xxHash (xxh3).** About ten times faster, not cryptographic; strict
  validation would need a second hash.
- **A content-defined chunking (rolling hash).** Survives insertions in the
  middle of a file, which FFAST does not need: a changed source invalidates
  its whole partial cache (spec §4.3).
- **Wait for the SQLite catalog (spec step 4) to hold progress.** The
  progress record is per scan and short-lived; the catalog will index
  finished caches. JSON plus append-only files needs no new dependency now.

## Consequences

- A cancelled 10 GB load reopened by the user parses only what was left.
- Hashing becomes chunk-parallel: the in-memory path can hash a column's
  chunks on several threads. SHA-256 is about 3.5x faster than md5 here, so
  hashing time should fall; to be measured.
- Each committed window writes up to 16,384 rows per column to the tail file
  (under 0.5 MB per per-atom float64 column) and calls `fsync` twice.
- Chunk digests are 32 bytes per 16,384 rows per column: about 0.2 MB per
  column for the 10 GB benchmark file. They are kept in the append-only file
  during a scan and in a sidecar file beside the manifest after it, for
  portable caches (spec step 6) and strict validation (step 7).
- Batches of an interrupted scan are ordinary cache files: unpinned and not
  open, so normal eviction may remove them; resume then starts from the
  beginning.
- Old saved sessions lose fingerprint-keyed settings, and one-off
  recalculation follows the upgrade.

## What the build settled

- A full load is named by the file alone only when the caller says it is a
  full, unsampled load (`full=True`; the one-pass scan and the reopen that
  finds its manifest). A strided or sampled load keeps its frame-list name.
- A finished scan writes `<identity>.chunks.npz` beside its manifest: for each
  column and dtype candidate a `(n_chunks, 32)` uint8 array, last row the
  digest of the short final chunk, so `column_digest_of_chunks` rebuilds every
  column digest. The manifest names it. It adds about 0.9 MiB to the cache of
  the 10 GB file.
- The scan lock file is deleted when the scan ends; a lock taken on a file that
  was deleted in the meantime is detected (inode check) and treated as held.
- The RAM cache is keyed by the cache identity, which no longer changes at the
  end of a scan, so a second scan in one process finds windows the first parsed.
- Per-frame columns need no extra file: `_save` writes level 1 (per-frame
  values, species, cell) in every batch, even when nothing else fits.
- If a window's batch cannot be written (disk allowance), the scan goes on
  without a record: nothing it could resume from would be valid.

## Validation

Benchmark comparison (ADR 0105 harness), 2026-11-18, M3 Pro, FFAST memory
budget 3 GiB, 5 repeats per side taking turns. Baseline `d2e3238f` (ADR 0106),
candidate `d4d25463` (this ADR, steps 1 to 5). Raw runs:
`temp/bench/2026-11-18-adr0107-small` (100 MB and 1 GB, all 12 cases) and
`temp/bench/2026-11-18-adr0107-10gb` (10 GB, medium atoms, constant and variable
counts). No case or measure reads *worse*.

| Case | Load, first open (s) | Load, reopen (s) | Basic Errors, first open (s) | Peak memory |
|---|---|---|---|---|
| 100 MB, six cases | 0.52-0.79 to 0.39-0.66 (ratio 0.74-0.83, better) | 0.31-0.56 to 0.30-0.44 (better in 3 of 6, else no clear change) | no clear change | no clear change |
| 1 GB, six cases | 2.50-2.68 to 1.25-1.41 (ratio 0.50-0.53, better) | 1.59-1.79 to 0.44-0.55 (ratio 0.28-0.31, better) | no clear change (one case better) | no clear change |
| 10 GB constant | 14.36 to 12.01 (0.836, better) | 0.34 to 0.30 (0.885, better) | 5.08 to 4.69 (no clear change) | 1502 to 1476 MiB (no clear change) |
| 10 GB variable | 14.56 to 12.05 (0.828, better) | 0.34 to 0.33 (no clear change) | 5.02 to 4.61 (no clear change) | 1524 to 1596 MiB (no clear change) |

Cache size on disk is unchanged to the megabyte (10 GB: 6649.2 to 6650.0 MiB,
the digest sidecar).

**Resume** (10 GB variable file, 3 repeats, load cancelled by the same stop
check the interface uses when 50 % of the bytes were read):

| | seconds |
|---|---|
| full load, nothing cached | 11.7, 12.0, 11.8 |
| cancelled load | 6.2, 6.2, 6.2 |
| reload after the cancel | 6.1, 5.8, 6.0 |

Each reload skipped 5.10 GB of 10.00 GB (51 %) and parsed only the rest; the
cancelled load plus its reload cost 12.2 s, within 4 % of one uninterrupted
load.

**Hashing** (one 10 GB variable load, wall time spent in `_observe` on the
parsing thread, and CPU time summed over the hashing threads): md5, 4.1 s wall
and 15.0 s CPU; chunked SHA-256, 1.4 s wall and 4.9 s CPU. The load itself went
from 14.5 s to 11.9 s. (py-spy is not installed; the figures come from timing
`_observe` and `_observe_key`.)

**Cost of the record** (same load): 75 commits took 0.25 s in total, 2.1 % of
the load, 3.3 ms each. About 0.07 s of that is formatting the 28,000 chunk-digest
JSON lines, so a binary layout would not bring it under 1 %; most of the rest is
the tail file and the flushes to disk. The JSON lines stay. The largest
`chunks.bin` was 2.8 MB and `progress.json` 3.3 KB.
