# NumPy against Zarr for the parsed batch cache: bench report

Status: final 2026-11-20. Rounds 1 and 2 warm; cold runs skipped (see below). Decision: NumPy
`.npz` batches stay; recorded in [ADR 0109](../adr/0109-numpy-and-zarr-batches-compete-in-a-standalone-bench.md).

## Setup

- Bench: `python -m benchmarks.storage` (`source`, `slice`, `run`, `report`), ADR 0109. Code in `benchmarks/storage/`.
- Machine: Apple M3 Pro (arm64), 18 GiB RAM, macOS 26.6.2, Python 3.11.14, NumPy 1.26.4.
- Versions: zarr 3.1.6, numcodecs 0.16.6. Zarr format 3. Blosc on one thread: `numcodecs.blosc.set_nthreads(1)`; it took effect (`blosc_nthreads` = 1 in every Zarr row). Zarr's own codec pool and chunk concurrency were also set to 1 (`threading.max_workers` = 1, `async.concurrency` = 1). That is a choice of this bench: Zarr's default would overlap chunk reads and decoding on several cores, which this bench does not measure (ADR 0109 section 4 asks for one Blosc thread because FFAST already reads on 4 lanes).
- Input: FFAST's own memory-saving batch set for each file (`source`), written through `python -m ffast.profiling` with `FFAST_MEMORY_BYTES` = 3 GiB. The 1 GB synthetic files and the 1 GB real slice came out as batches only in memory-saving mode (FFAST's automatic mode gave whole arrays), so `source` loaded them again in that mode. The 10 GB files and the full real file took the batch path automatically. Batches have no omitted columns. The small `-1--1` file (energy columns, duplicated in each batch) is left out.
- Files: synthetic 1 GB and 10 GB, medium atoms, constant and variable atom counts (ADR 0105 generator); real data is the 16 GB SO3LR training file (not in the repository): a 1 GB slice of whole frames from its start (149,364 frames) for round 1, the whole file (3,856,645 frames, about 20 atoms each) for round 2.
- Each timed operation ran in a fresh process; variants took turns, order rotating between repeats; 5 repeats; warm (the store was read once before each timed read). Every variant was checked bit for bit against the source before it was timed; all passed.
- Reading a window of the source is not timed during `write`; its writing and the `fsync` of every new or changed file are. The source windows are read one at a time (not all into RAM first), because the 10 GB and 16 GB sets do not fit in 18 GiB RAM.
- Disk bytes are the sum of file sizes of the store folder.

## A/A check

`npz` against `npz-copy` (same code), 1 GB medium constant, 5 repeats: every measure reads "no clear change". The noise is small enough to trust 10 % verdicts.

## Round 1 (all 16 variants, warm, 5 repeats)

Medians as a ratio to `npz` (below 1.00 is better for every column). Real 1 GB slice:

| Variant | disk | write | scan | frame p95 | reopen | peak mem |
|---|---:|---:|---:|---:|---:|---:|
| npz | **1.00** (478 MiB) | 1.00 | 1.00 | 1.00 | 1.00 | 1.00 |
| zarr-none-16k | 0.98 | 6.70 | 3.49 | 5.24 | 4.98 | 1.09 |
| zarr-none-128k | 1.00 | 4.01 | 0.91 | 5.44 | 4.91 | 0.83 |
| zarr-none-1m | 1.20 | 11.21 | 3.06 | 55.48 | 22.42 | 1.23 |
| zarr-lz4-16k | 0.89 | 10.65 | 6.74 | 7.56 | 12.42 | 1.27 |
| zarr-lz4-128k | 0.88 | 7.37 | 3.74 | 20.51 | 17.22 | 1.29 |
| zarr-lz4-1m | 0.88 | 18.76 | 8.64 | 151.86 | 74.81 | 3.26 |
| zarr-zstd1-16k | 0.88 | 13.33 | 7.93 | 9.20 | 12.00 | 1.21 |
| zarr-zstd1-128k | 0.88 | 10.34 | 5.17 | 33.74 | 18.90 | 1.29 |
| zarr-zstd1-1m | 0.88 | 25.40 | 11.41 | 253.03 | 95.86 | 3.15 |
| zarr-zstd3-16k | 0.87 | 14.74 | 7.24 | 8.52 | 12.11 | 1.26 |
| zarr-zstd3-128k | 0.87 | 11.34 | 4.55 | 27.62 | 18.85 | 1.29 |
| zarr-zstd3-1m | 0.87 | 26.88 | 9.86 | 204.24 | 84.08 | 3.21 |
| zarr-zstd5-16k | 0.86 | 20.71 | 7.15 | 8.69 | 11.97 | 1.32 |
| zarr-zstd5-128k | 0.85 | 18.46 | 4.56 | 29.47 | 21.17 | 1.28 |
| zarr-zstd5-1m | 0.85 | 40.44 | 10.43 | 224.84 | 94.38 | 3.23 |

Synthetic 1 GB, medium constant (random positions barely compress, so disk gain is pessimistic here):

| Variant | disk | write | scan | frame p95 | reopen | peak mem |
|---|---:|---:|---:|---:|---:|---:|
| npz | **1.00** (665 MiB) | 1.00 | 1.00 | 1.00 | 1.00 | 1.00 |
| zarr-none-16k | 1.00 | 5.76 | 3.18 | 5.41 | 4.99 | 1.14 |
| zarr-none-128k | 1.02 | 3.87 | 0.94 | 5.33 | 4.95 | 1.06 |
| zarr-none-1m | 1.25 | 9.23 | 2.14 | 55.94 | 22.08 | 1.50 |
| zarr-lz4-16k | 0.94 | 9.56 | 6.14 | 7.86 | 11.57 | 1.13 |
| zarr-lz4-128k | 0.92 | 7.38 | 3.18 | 20.59 | 16.88 | 1.43 |
| zarr-lz4-1m | 0.92 | 16.22 | 5.74 | 154.33 | 73.17 | 3.40 |
| zarr-zstd1-16k | 0.91 | 13.04 | 6.94 | 8.46 | 11.74 | 1.13 |
| zarr-zstd1-128k | 0.91 | 10.97 | 4.27 | 25.54 | 19.51 | 1.44 |
| zarr-zstd1-1m | 0.91 | 21.94 | 7.49 | 199.68 | 90.70 | 3.53 |
| zarr-zstd3-16k | 0.90 | 15.03 | 6.67 | 8.06 | 11.89 | 1.12 |
| zarr-zstd3-128k | 0.90 | 12.54 | 3.67 | 23.05 | 17.89 | 1.43 |
| zarr-zstd3-1m | 0.90 | 23.84 | 6.39 | 175.47 | 81.38 | 3.17 |
| zarr-zstd5-16k | 0.89 | 26.32 | 7.07 | 8.52 | 11.85 | 1.13 |
| zarr-zstd5-128k | 0.89 | 22.85 | 4.85 | 29.73 | 20.37 | 1.42 |
| zarr-zstd5-1m | 0.89 | 39.62 | 8.23 | 226.76 | 104.06 | 3.38 |

The synthetic variable file gives the same picture as the constant one.

Survivors of round 1: **none**. Every Zarr variant is worse than `npz` by more than the 10 % alarm, with non-overlapping quartiles, on write time, scan time, frame p95 and reopen time on all three files. The measure that ruled each variant out first, and the ranges, are in the report's "Rule-out" list (`python -m benchmarks.storage report temp/storage-bench/round1`). Compact version: write is 4x to 45x slower, full scan 2x to 11x slower, frame p95 5x to 250x slower, reopen 5x to 105x slower. Peak memory is also worse (up to 5x for 1 Mi-row chunks). `zarr-none-128k` is the closest to `npz` (scan about equal) but still loses on write, frame p95 and reopen. `zarr-none-1m` is also dominated on the real slice.

Disk saving on the real 1 GB slice is 12 % to 15 % for the compressed variants (best `zarr-zstd5-128k` and `zarr-zstd5-1m`, 0.85 of `npz`), 2 % for uncompressed 16k and 128k chunks, and the uncompressed 1 Mi-row variant is 20 % larger (cause not investigated). No variant reaches the 25 % go line.

## Round 2

Because no variant survived round 1, round 2 ran `npz` and the two Zarr variants with the fewest bytes on the real slice (as the plan says): `zarr-zstd5-128k` and `zarr-zstd5-1m`. Warm, 5 repeats.

Full real file (ratios to `npz`):

| Variant | disk | write | scan | frame p95 | reopen | peak mem |
|---|---:|---:|---:|---:|---:|---:|
| npz | **1.00** (7222 MiB) | 1.00 | 1.00 | 1.00 | 1.00 | 1.00 |
| zarr-zstd5-128k | 0.80 | 15.73 | 4.16 | 14.85 | 10.12 | 1.23 |
| zarr-zstd5-1m | 0.80 | 31.75 | 8.92 | 113.20 | 40.27 | 3.00 |

Absolute medians, full real file:

| Variant | disk (MiB) | scan (s) | frame p95 (ms) | write (s) | reopen (ms) |
|---|---:|---:|---:|---:|---:|
| npz | 7222 | 1.44 | 0.95 | 3.85 | 2.8 |
| zarr-zstd5-128k | 5757 | 5.99 | 14.08 | 60.54 | 28.0 |
| zarr-zstd5-1m | 5757 | 12.82 | 107.32 | 122.18 | 111.6 |

Synthetic 10 GB medium constant:

| Variant | disk | write | scan | frame p95 | reopen | peak mem |
|---|---:|---:|---:|---:|---:|---:|
| npz | **1.00** (6637 MiB) | 1.00 | 1.00 | 1.00 | 1.00 | 1.00 |
| zarr-zstd5-128k | 0.89 | 17.79 | 4.43 | 16.39 | 15.98 | 1.62 |
| zarr-zstd5-1m | 0.89 | 30.42 | 7.35 | 125.50 | 77.88 | 4.65 |

Synthetic 10 GB medium variable:

| Variant | disk | write | scan | frame p95 | reopen | peak mem |
|---|---:|---:|---:|---:|---:|---:|
| npz | **1.00** (6631 MiB) | 1.00 | 1.00 | 1.00 | 1.00 | 1.00 |
| zarr-zstd5-128k | 0.89 | 20.87 | 4.26 | 16.43 | 12.10 | 1.94 |
| zarr-zstd5-1m | 0.89 | 37.53 | 7.16 | 125.47 | 58.71 | 6.63 |

On the full real file the best Zarr variant stores 5,757 MiB against 7,222 MiB for `npz`: **20 % fewer bytes**, short of the 25 % go line. Its full scan is 4.2x slower, frame p95 14.8x slower (14.1 ms against 0.95 ms), write 15.7x slower and reopen 10x slower.

## Cold runs (skipped)

Cold runs need `sudo purge`, which asks for a password on this machine. They were skipped on
purpose: under ADR 0109 §5 a variant reaches the go test (where a cold scan at least 10 %
faster would count) only after it survives the warm rule-out, and none did. A cold result could
not change the decision. To measure them anyway, from the repository root in one terminal:

```
sudo -v
( while true; do sudo -n true; sleep 60; kill -0 "$$" 2>/dev/null || exit; done ) &
python -m benchmarks.storage run --cold \
  --source temp/storage-bench/src-1gb-real --source temp/storage-bench/src-10gb-medium-constant \
  --variants npz,zarr-zstd5-128k,zarr-zstd5-1m --repeats 5 --output temp/storage-bench/round2-cold
python -m benchmarks.storage report temp/storage-bench/round2-cold
```

`run --cold` refuses to start when `sudo -n true` fails. Recreate the sources with
`python -m benchmarks.storage source` if they were deleted.

## Limits of this result

- One machine (laptop SSD). Lustre on the cluster was not measured; there, smaller files could
  matter more. The gaps (scan 4x, frame p95 15x on the real file) are too large for that to
  flip the result without new evidence.
- Zarr ran with one decoding thread and no overlapping chunk reads. Its defaults would use more
  cores, which FFAST needs for the parser and its 4 metric lanes.
- Peak memory and the uncompressed 1 Mi-row variant being 20 % larger were not investigated.

## Decision

NumPy `.npz` batches stay. No Zarr variant survives the rule-out: all are more than 10 % worse
than `npz` on write, scan, frame p95 and reopen on every file, and most also on peak memory.
The best disk saving on the real file is 20 % (zstd level 5), under the 25 % go line, at the cost
of a 4.2x slower full scan and 14.8x slower frame clicks. No integration ADR follows.
