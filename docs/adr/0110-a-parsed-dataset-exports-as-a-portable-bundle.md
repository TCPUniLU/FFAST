# A parsed dataset exports as a portable bundle

Status: Accepted and implemented 2026-11-20. Builds on ADRs 0103, 0106, 0107 and 0108.
Spec v1.1 §4.2 (portable caches), step 6 of the agreed order.

FFAST's parsed copies of a large `.xyz` file only work on the machine, and
at the path, where they were made:

- A batch store is named by a hash of the absolute path, inode, size and
  modification time (`IndexedStore.__init__`); a whole-array entry by a hash
  of the absolute path, size and modification time (`frame_cache._key`).
  Copied anywhere else, nothing finds them.
- Every read calls `IndexedStore.check_source()`, which fails when the
  source file is gone, even if every value it asks for is already parsed.

Spec §4.2 asks for portable caches: explicit dtypes and byte order, a schema
version separate from the app version, relative paths, self-contained
metadata, and use without the original file for every field that was
cached.

Example of the wanted behaviour. On MeluXina a user loads
`so3lr_100GB.xyz`, then runs
`ffast-cli cache export so3lr_100GB.xyz -o ~/project/so3lr.ffast`. They
`rsync` the folder to a laptop; the 100 GB file stays on the cluster. On the
laptop they open `so3lr.ffast/dataset.ffast.json` in the normal open box.
Energies, forces and positions show, the dataset fingerprint is the one the
cluster had, so a saved session finds its metric results. Anything that
needs the text of the file (a damaged batch, say) names the missing source
file and stops.

## Decision

- **A bundle is a folder the user owns.** `ffast-cli cache export PATH -o
  DIR` writes a folder of plain files. The parsed cache in
  `~/.ffast/frame-arrays` is unchanged: it stays keyed by path and file state
  for fast change detection (spec §11a item 3), and its pins and eviction
  (ADR 0108) never see bundles. A bundle is never evicted and does not count
  against `FFAST_PARSED_CACHE_BYTES`.
- **Layout.** All names are relative to the folder:

  ```
  so3lr.ffast/
    dataset.ffast.json   description (below); written last
    counts.npy           atoms per frame, int64
    source-offsets.npy   byte start of each frame in the source, plus the end; int64
    chunks.npz           chunk digests of every column (ADR 0107 sidecar)
    batches/0-4096.npz   one uncompressed .npz per frame range, every column
    ...
  ```

  `dataset.ffast.json` holds: `"schema": 1`; the reader name and its cache
  version; the fingerprint scheme tag (`ffast-chunks-v1-sha256-16384`); the
  reader's `contents()` (energy, force and field names, units); the frame
  ranges with each batch file's name and byte size; for each column its dtype
  string with byte order (`"<f8"`), row shape, digest and float64 digest; the
  per-dataset facts the manifest already keeps (first frame's numbers,
  uniform, same formula, element set); and a `source` record (below). Arrays
  are `.npy` members, which carry dtype and byte order themselves.
- **Every bundle is complete.** Export writes every column the reader
  discovered, for every frame, at full level, whatever the local cache holds:
  batches that are in the cache and complete are copied, the rest are parsed
  from the source. It writes into the target folder directly, so the parsed
  cache budget and its eviction do not apply; export only checks free space
  at the target first (estimated bytes plus 10 %), and refuses if it is short.
  Export is always a full, unsampled load of one source file. A separate
  prediction file is exported to its own bundle.
- **Export is atomic.** It writes into `DIR.part-<pid>/` beside the target
  and renames it to `DIR` when `dataset.ffast.json` is written. A cancelled
  or failed export deletes the temporary folder. An existing `DIR` is
  refused unless `--force`, which replaces it only after the new one is
  complete.
- **Opening a bundle needs no source.** A new bundled reader, named
  `ffast-bundle`, takes files called `*.ffast.json`. It builds a store that
  reads only from the bundle's batches: the same column reads, slice reads in
  place and RAM cache as `IndexedStore`, but no scan, no saving, no manifest
  writes, no locks, and no source check while every batch is present. The
  bundle is read-only; FFAST never writes into it. Its RAM-cache identity is
  a hash of the scheme tag and the column digests, so two copies of the same
  bundle share it.
- **Same fingerprint.** The column digests come from the bundle, so the
  dataset fingerprint (`Dataset.getFingerprint`, ADR 0058) equals the one of
  the original load. On open, FFAST rebuilds each column digest from
  `chunks.npz` and compares it with the stored one; this reads no data.
- **Checks on open (fast policy, spec §4.3).** The schema is known; every
  listed batch exists with its recorded byte size; the chunk digests agree
  with the column digests. A failed check stops the open with a message
  naming what is wrong. The strict check of the batch contents against the
  chunk digests is step 7.
- **When the source is needed.** A batch that turns out to be damaged on
  read, and any later feature that needs the text of a frame, need the source.
  The `source` record holds the file name, the original absolute path, size,
  modification time, and the SHA-256 of eight 1 MiB slices (first, last and
  six evenly spaced; the whole file when it is smaller than 8 MiB). FFAST
  looks at the original path, then for a file of that name beside the bundle
  folder, and accepts the first one whose size and slice hashes match (well
  under 0.1 s). That is a quick check, not proof. With no match, the read
  fails with a message naming the file and both places it looked.
- **Schema rules.** `schema` is the bundle format version, independent of
  the FFAST version. A bundle with a higher schema than this FFAST knows is
  refused with a message to update FFAST. A later schema change that adds
  description fields only keeps the batches readable; one that changes batch
  layout bumps the number.
- **Loads of a bundle are full.** A strided or sampled load of a bundle is
  refused with a message; the bundle opens in memory-saving form, which
  already handles any size.
- **Scope.** XYZ files read by the fast path (the one-pass scan and the
  indexed store), the only ones with batch stores. The CLI is the only way to
  export; a browser action comes later, after a sketch. Pins by content,
  bundles listed in `ffast-cli cache list`, and importing a bundle into the
  parsed cache are not part of this decision.

## Alternatives

- **Make the parsed cache folder itself movable** (content names, relative
  paths). No export step, but users would copy the whole cache instead of one
  dataset, and the cache would need content lookup on every open, which the
  path-and-state key avoids today.
- **Both a movable cache and bundles.** Most flexible; about twice the code
  and tests for a case nobody has asked for.
- **Require the source to open a bundle.** Simple, but fails spec §4.2 and the
  cluster-to-laptop case that motivates portability.
- **Export copies only what the cache holds.** Faster when the cache is
  partial, but bundles could lack fields, and both the whole-array and the
  batch layout would need an exporter and a bundle reader.
- **Refuse export of a partial cache.** Simplest, but the cache budget often
  forces partial copies of exactly the large files worth exporting.
- **Match a source by size and name only.** Cheapest, but a regenerated file
  of the same size passes.
- **Match a source by a full hash of its text.** Proof, but reads the whole
  100 GB file, the cost bundles exist to avoid; the strict check (step 7)
  can offer it.
- **One archive file instead of a folder** (zip or tar). One thing to move,
  but `.npz` members inside an archive cannot be read in place without
  extraction, and `rsync` resumes a folder better. Users can still `tar` the
  folder.
- **Zarr as the bundle format.** Lost on every measure in the step 5 bench
  (ADR 0109).

## Consequences

- A dataset parsed on the cluster can be studied on a laptop without the
  source file, with the same fingerprint, so sessions and metric results
  follow it.
- Disk use doubles while both the cache copy and the bundle exist; the user
  decides when to delete either.
- A bundle is about as large as the parsed cache of a complete load (about
  6.6 GB for the 10 GB benchmark file) plus 8 bytes per frame for the source
  offsets and the chunk digests (under 1 MiB for 10 GB).
- The bundle store is a second store class beside `IndexedStore`; shared
  reading code must stay shared, not copied.
- Export of a file the cache does not hold costs one full parse.
- A moved source file still loses its pin and its cache copy; only bundles
  are portable.

## Validation

Measured 2026-11-20 on the laptop (11 cores, 18 GB, memory budget 3 GiB), with
the ADR 0105 harness's medium, constant-count files. `python -m benchmarks.ingest
bundle` (new, in `benchmarks/ingest/bundle.py`) times the export, the bundle and
the parsed cache; the figures are medians with the range in brackets.

| File | Repeats | Cold export | Warm export | Bundle size |
|---|---|---|---|---|
| 1 GB (0.93 GiB) | 5 | 1.4 s (1.3-1.9) | 0.20 s (0.20-0.20) | 0.65 GiB |
| 10 GB (9.32 GiB) | 3 | 19.3 s (16.2-21.0) | 9.9 s (5.2-27.2) | 6.49 GiB |

Cold means an empty parsed cache (one full parse plus the copy); warm means the
cache the cold export left, so complete batches are copied. The warm 10 GB time
is a disk copy and varies with the disk (5.2 s to 27.2 s).

Open plus Basic Errors, bundle against a reopen from the parsed cache (the
profiler's second run), in seconds:

| File | Bundle: open + Basic Errors | Parsed cache reopen: open + Basic Errors |
|---|---|---|
| 10 GB | 0.40 (0.39-0.74) + 4.88 (4.87-6.06) | 0.35 (0.35-0.35) + 4.87 (4.68-4.88) |
| 1 GB | 0.18 (0.10-0.19) + 0.86 (0.84-0.94) | 0.44 (0.44-0.47) + 0.54 (0.48-0.73) |

At 10 GB the two match within the spread, as expected: both read batches in
place. At 1 GB the parsed cache is not like for like: under a 3 GiB budget an
ordinary load of a 1 GB file holds every array in memory (peak 3.3 GiB), while
a bundle always opens in memory-saving form (peak 0.4 GiB), so it opens faster
and reads slightly slower. Source file deleted: a bundle of the 1 GB file opened
and ran Basic Errors with the source renamed away; nothing asked for it.

Ordinary loads, baseline `17b2a179` (before this work) against this checkout,
`--sizes 100MB,1GB --classes small,medium --counts constant,variable
--repeats 5`: every row of load time, Basic Errors time, peak memory and cache
size, for the first open and the reopen, reads "no clear change" (32 rows each).

Fingerprint of the bundle equals the fingerprint of the original load for
constant and variable atom counts and for a file with in-file predictions (the
prediction's energy and force included); tests in
`tests/ffast/readers/test_bundle_open.py`. A server loads a bundle, scores it,
saves a session and a fresh server restores it
(`tests/ffast/renderers/web/test_web_bundle.py`).

## Deviations from the plan

The bundle store is a subclass of `IndexedStore` (`ffast/readers/bundle_store.py`);
the parse of a window moved into `IndexedStore._parse` so both can call it.
`chunks.read_arrays` calls a reader's `load_whole` when it has one, and ASE's
`can_read` leaves `*.ffast.json` to the bundle reader. A bundle opens with a
selection that is every frame (a prediction loaded from a bundle is asked that
way); any other selection or a stride above 1 is refused. The first build read
every energy a frame at a time from the batch files and Basic Errors took 13 s
on the 1 GB file; the store now keeps the per-frame values in memory at open
(as the parsed cache does) and it takes 0.8 s. The browser hand check was run as
a headless profiler session and a protocol test, not by hand in a browser.
