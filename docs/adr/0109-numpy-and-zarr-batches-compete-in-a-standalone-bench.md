# NumPy and Zarr batches compete in a standalone bench first

Status: Accepted 2026-11-20; result: NumPy batches stay. Builds on ADRs 0105, 0106, 0107 and 0108.
Spec v1.1 step 5 (§4.4, §8 selection rule, §11a item 2).

The parsed cache of a large `.xyz` file is a set of batch files,
`~/.ffast/frame-arrays/batches/<identity>-<a>-<b>.npz`, about 128–256 MiB
each. Each holds plain, uncompressed NumPy arrays packed in a zip
(`np.savez`, `ZIP_STORED`). FFAST reads only the rows it needs by seeking
into the zip member (`IndexedStore._cached_slice`, ADR 0106). This format
was chosen without a measurement (spec §1.4, row §4.4).

Zarr is the common alternative for large numeric arrays: each array is cut
into chunks, each chunk is a separate file (or a slice of a shard file), and
each chunk can be compressed. It could help FFAST in two ways:

- **Smaller cache.** Forces and positions are float64. Compression with a
  byte shuffle often saves 10–40 % on simulation data. A smaller cache means
  more datasets fit in the 20 GiB parsed-cache budget, and fewer bytes to
  read on a slow disk such as the cluster's Lustre.
- **Chunks that line up with fingerprints.** ADR 0107 hashes columns in
  16,384-row chunks; Zarr chunks of the same size could later make strict
  validation (spec §4.3) cheaper.

It could also hurt: compressing costs CPU while the parser runs, every read
decompresses whole chunks (a frame click reads one chunk, not just its own
rows), many small chunk files are slow on network file systems, and it adds
a dependency to the server.

Worked example. `water.xyz` (10 GB) gives about 75 batches, 6.8 GB on disk.
Basic Errors reads every force column once; clicking frame 51,234 reads that
frame's positions and forces. If Zarr with fast compression stored the same
data in 4.5 GB, read it back no slower, and showed a frame no slower, it would
win. If it saved 30 % of disk but made Basic Errors 20 % slower, it would not:
the spec ranks first plot, ingestion, reopen, memory and frame access above
disk size (§8, §11a item 2).

## Decision

### 1. A standalone bench decides first

A new bench, `python -m benchmarks.storage`, takes the batch set FFAST
itself writes for a file, rewrites the same arrays in each candidate format,
and times the same reads FFAST does. FFAST's runtime code does not change in
this step. Zarr enters FFAST only if the bench shows a candidate that passes
rule 5; that would be a separate ADR and plan.

### 2. Candidates: today's batches against a Zarr sweep

- **Baseline:** today's `.npz` batches, read with the same in-place row
  reads FFAST uses (`_member_rows`).
- **Zarr** (version 3 format, the `zarr` 3.x package), one group per
  dataset, one array per column, rows appended window by window as a scan
  would. Sweep:
  - codecs: none; Blosc LZ4 with byte shuffle; Blosc zstd levels 1, 3 and 5
    with byte shuffle;
  - chunk lengths: 16,384 rows (the ADR 0107 hash chunk), 131,072 rows and
    1,048,576 rows;
  - 16,384-row chunks are stored in shards of 64 chunks, so the file count
    stays comparable to the larger chunks.

  That is 15 Zarr variants. Blosc runs on one thread: FFAST already reads
  in 4 parallel metric lanes and the parser uses the other cores.

### 3. Data: synthetic and one real file

- Synthetic files from the ingestion harness (ADR 0105): 1 GB and 10 GB,
  medium atom class, constant and variable atom counts. Random positions
  barely compress, so these show the cost side fairly and the gain side
  pessimistically.
- One real file: the 16 GB SO3LR training file used in the 2026-11-15
  profile (local drive, not in the repository). Real simulation data shows
  what compression really saves. A 1 GB slice of whole frames from its start
  is used in round 1.

### 4. Measures and rounds

Per variant and file, each run in a fresh process:

| Measure | Stands for |
|---|---|
| write wall and CPU seconds, window by window, `fsync` per window | extra cost during a first load |
| full scan of every `forces:` column, wall and CPU | Basic Errors on a reopened dataset |
| 2,000 random frames (fixed seed), positions + forces, p50 and p95 | frame clicks |
| reopen: open the store and read frame 0 | second open of a dataset |
| bytes on disk and number of files | cache budget, network file systems |
| peak memory of the process | memory safety |

Warm runs read with the files in the operating system's file cache. Cold
runs follow `sudo purge` (macOS needs a password, so the user runs them).
Median, quartiles and p95 over at least 5 runs; sides take turns as in
ADR 0105. Results are Parquet rows queried with DuckDB, like the ingestion
harness.

- **Round 1:** all 16 variants on the two 1 GB synthetic files and the 1 GB
  real slice, warm, 5 runs.
- **Round 2:** the baseline plus every Zarr variant that round 1 did not
  rule out, on the 10 GB synthetic files and the full real file, warm and
  cold, 5 runs.

Every variant must return arrays bit-identical to the baseline before it is
timed (KPI-06).

### 5. Decision rule

A Zarr variant is ruled out when the baseline is no worse on every measure
(dominated, spec §8), or when it is worse than the baseline by more than the
spec's alarm on any of: write time (10 %), scan time (10 %), random-frame
p95 (10 %), reopen (10 %) or peak memory (10 %), judged with the ADR 0105
verdict (ratio past the alarm and quartile ranges that do not overlap).

A surviving variant goes on to an integration ADR only if it also brings a
clear gain on the real file: at least 25 % fewer bytes on disk, or a cold
full scan at least 10 % faster. Otherwise NumPy batches stay and this ADR
records why.

### 6. What stays in the repository

Whatever the result: the bench under `benchmarks/storage/`, `zarr` only in
the optional `bench` extra (never a runtime dependency unless an
integration ADR adds it), a report in `docs/reports/`, and the result in
this ADR. The spec's §1.4 row for §4.4 is updated.

## Result (2026-11-20)

[Report](../reports/2026-11-20-numpy-vs-zarr-batches.md). No Zarr variant survived round 1:
all 15 were more than 10 % worse than `npz` on write (4–45x), full scan (2–11x), frame p95
(5–250x) and reopen (5–105x) on every 1 GB file. On the full 16 GB real file, the two variants
with the fewest bytes (zstd level 5, 128k and 1 Mi-row chunks) stored 20 % fewer bytes than
`npz` (5,757 against 7,222 MiB) but scanned 4.2x and 8.9x slower, with frame p95 of 14 ms and
107 ms against 0.95 ms. The 20 % saving is under the 25 % go line, so **NumPy `.npz` batches
stay** and no integration ADR follows. Cold runs were skipped: a variant needs to survive the
warm rule-out before a cold gain can count, and none did.

Deviations from the plan, kept: Zarr's own codec pool and chunk concurrency were also set to
1 (with Blosc on one thread); source windows are read one at a time during `write` because the
10–16 GB sets do not fit in RAM; 1 GB files were sourced in memory-saving mode because the
automatic mode gave whole arrays.

## Consequences

- One measured answer to "why not Zarr?", rerunnable on a new machine or on
  the cluster with one command.
- No runtime risk in this step; the cost is bench code and about a day of
  machine time (round 2 on 10–16 GB files, cold runs included).
- If Zarr wins, the integration (scan writes, `_cached_slice`, eviction in
  ADR 0108, chunk digests in ADR 0107, resume) is a second, larger piece of
  work, planned with these numbers in hand.
- The bench times arrays through Python, outside a running server: thread
  contention with the parser is approximated by single-threaded Blosc, not
  measured. An integration ADR must confirm the win with the ingestion
  harness (ADR 0105).

## Considered and rejected

- **Build Zarr into `IndexedStore` behind a switch first.** Real numbers from
  the full harness, but costs the integration work even if Zarr loses.
- **Three variants only** (none, zstd 1, baseline). Cheaper; the user chose
  the full sweep so that a "no" covers the usual settings.
- **Synthetic data only.** Reproducible, but understates compression.
- **HDF5 and TileDB.** Dropped from Phase 1 in spec v1.1 §11a item 2.
- **A runtime dependency on Zarr now.** Only after an integration ADR.
