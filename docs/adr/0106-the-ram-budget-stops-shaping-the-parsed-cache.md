# The RAM budget stops shaping the parsed cache

Status: Accepted and implemented 2026-11-18. Amends ADRs 0102 and 0103.

The same 10 GB XYZ file left a 4.7 GB or a 7.0 GB parsed cache, and loaded by
a different path, depending on how much RAM happened to be free when it was
opened. Two budget rules caused this. A file larger than twice the budget was
loaded in one pass (ADR 0103), which never cached positions; a smaller one was
indexed first, read in budget/32 windows, and cached positions when they fit.
The budget was 80% of free RAM at start, so on one laptop it ranged from 3.8
to 5.5 GB between runs, and the 10 GB file crossed the 5.0 GB switch.

FFAST also kept its own RAM cache of batches read back from disk, sized by
that budget. The operating system already keeps recently read file data in
RAM and drops it under memory pressure, so this cache duplicated its work.

## Decision

- **One loading path for memory-saving mode.** Every full, unsampled XYZ load
  in memory-saving mode finds frames and parses them in one pass (ADR 0103),
  whatever the budget, even when a frame index is already on disk. The
  "larger than twice the budget" test no longer chooses the path; it only
  lets an automatic load skip building a frame index when the file is
  clearly too large for full arrays. A smaller file is indexed to decide;
  if it does not fit, it still loads by the one-pass path. A reopen with a
  saved manifest opens from it without scanning. Strided and sampled loads
  keep their indexed path.
- **The cache keeps columns by file size and disk space, not RAM.** The
  one-pass load projects the final cache size from the bytes parsed so far
  and the file's total size, so it keeps positions (priority level 3 in
  ADR 0103) when the projected total fits the disk allowance. The allowance
  is read once per scan. The kept level may drop when the projection grows,
  and never goes back up. The same file on the same machine always leaves
  the same cache.
- **Cached batches are read with plain reads, straight into the result
  array.** The `.npz` members are uncompressed, so a read seeks to the rows
  it needs and reads them into a new array: one system call, one copy. The
  cache file format does not change.
- **FFAST's own RAM cache stops holding batches read from disk.** Recently
  read cache data stays in RAM as operating-system file cache, which is
  dropped first when memory is short. The RAM cache keeps only windows
  parsed from the source (columns not on disk), so reference and prediction
  reads of one window still share one parse (ADR 0103).
- **The automatic budget uses a stable figure.** A workstation's default is
  80% of total RAM, minus 512 MiB headroom, instead of 80% of RAM free at
  start; a visible job memory limit still takes priority (ADR 0102). The
  full-array vs memory-saving choice compares the estimate with this limit
  only. Each reservation still checks the memory free at that moment; a
  full-array load refused there starts again in memory-saving mode.

## Considered options

- **Keep the budget rules, only align the two paths.** Smallest change, but
  the cache contents would still follow free RAM through the fits/doesn't-fit
  choice.
- **Memory maps, opened and closed per read, with a read-ahead hint
  (`madvise(MADV_WILLNEED)`).** Built first. One thread reading whole
  batches was up to 3x faster than the old reads, but Basic Errors on the
  10 GB file became 2.6x slower (see Evidence). Mapping saves a copy only
  when data is used in place; FFAST copied it out at once, so the map added
  setup, a page fault per page and a cross-core cleanup on every close.
  Threads opening and closing maps at the same time waited on each other:
  CPU use during Basic Errors fell from 3.4 to 2.1 cores.
- **One map per file kept open.** The only form in which maps would pay off,
  for example for random frame jumps. Process RSS then includes every page
  read (9 GB for a 9 GiB map on MeluXina), which RSS-based accounting such as
  `slurm_headroom` would read as memory in use.
- **A new cache format (Zarr, plain `.npy` per column).** Would let columns
  grow while scanning; the projected-size rule removes that need. Zarr is
  compared separately (spec step 5).
- **A general memory manager (Dask distributed).** Would replace FFAST's
  executor; far larger than the problem.

## Consequences

- The 10 GB benchmark file leaves a 6.6 GB cache instead of 4.5 GB, because
  positions now fit the disk allowance; the 20 GiB parsed-cache limit still
  applies.
- First-open load of that file is about 7% slower (13.3 s → 14.1 s), the cost
  of writing the larger cache. Basic Errors and reopen are unchanged.
- Peak RSS no longer includes batches read back from disk, so it stops being
  a measure of how much cache FFAST reads; memory reports say so.
- A budget from total RAM can admit a full-array load while other apps use
  much of that RAM, so the system may swap where before the load would have
  gone to memory-saving mode. This is the price of a stable choice; users
  can still set `FFAST_MEMORY_BYTES` or force memory-saving mode.

## Evidence (2026-11-18)

10 GB synthetic file `10GB-medium-variable-s0-g1` (344,760 frames,
94,750,336 atoms), M3 Pro laptop with 18 GB RAM, benchmark harness (ADR
0105) with a 3 GiB budget unless stated.

- **Before:** budget 4 GiB gave a one-pass load, 75 batches, no positions,
  4.58 GB cache. Budget 5.5 GiB gave a 2.9 s index pass, 55 batches,
  positions cached, 6.80 GB cache.
- **After:** budgets 3 GiB and 5.5 GiB both gave 77 batches with identical
  file names and sizes, positions cached, 6.81 GB cache. Load took 15.4 s and
  19.2 s in that single pair; the spread was not investigated.
- **Old code vs memory maps** (medians; 3 runs at 10 GB):

  | File | Measure | Old | Maps |
  |---|---|---|---|
  | constant | load, first open | 13.20 s | 13.99 s |
  | constant | Basic Errors, first open | 4.58 s | 12.06 s |
  | constant | Basic Errors, reopen | 4.46 s | 7.83 s |
  | variable | load, first open | 13.27 s | 14.18 s |
  | variable | Basic Errors, first open | 4.64 s | 12.25 s |
  | variable | Basic Errors, reopen | 4.54 s | 8.09 s |

- **Maps vs plain reads**, same code otherwise, variable file, 5 alternating
  runs each: Basic Errors 12.02 s → 4.61 s (first open) and 7.96 s →
  4.52 s (reopen); load 14.09 s vs 14.06 s; peak memory and cache size
  unchanged.
- **Random frames**, 2,000 single-frame reads from the 6.8 GB cache: plain
  reads 0.12 s from RAM and 0.68 s from disk; maps per read 0.30 s and
  0.89 s; maps with the hint 0.59 s from disk. Every method stays below
  0.5 ms per frame.
- 100 MB and 1 GB files load as full arrays on both sides (old code vs
  maps, 5 runs). Load showed no clear change anywhere. A few medians moved
  past 10% in both directions: Basic Errors 0.84x to 1.18x (0.35 s → 0.41 s
  at most), one reopen 1.10x (0.31 s → 0.34 s), peak memory up to 1.15x.
  Read as noise; not rerun.

## Open questions

- **`slurm_headroom` trusts `SLURM_MEM_PER_NODE`** even where it is not
  enforced, which may hold FFAST to 4 GB on a 480 GB node. Separate issue.
- Cold-disk reads were not repeated with the final code (purging the file
  cache needs `sudo`).
