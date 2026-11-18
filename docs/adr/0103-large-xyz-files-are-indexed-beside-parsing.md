# Large XYZ files are indexed beside parsing, and scans preserve their cache

Status: Accepted and implemented 2026-11-15. Extends ADRs 0100 and 0102.

Profiling a 16 GB file on a laptop found that memory-saving loads read the
source twice, metrics re-parsed the source on every run, cache file names
depended on the current RAM budget, and large scans evicted the cached
batches they needed next. More parser threads would not remove that repeated
work. Large XYZ loads therefore find frames and parse them in one pass, and
the parsed cache is arranged so later scans reuse it.

## Decision

- **One pass for large full-file loads.** When an unsampled XYZ file is larger
  than twice the shared RAM budget and the compiled frame finder is available,
  find frames and parse them in bounded windows side by side: at most one
  finder window and one parser window at a time, each at most 128 MiB and one
  sixteenth of the budget. Each window is parsed while its text is still in
  the OS page cache. Unusual frame-count syntax falls back to the Python
  scanner without publishing a partial dataset. Stride and sampled loads keep
  their exact indexed path.
- **Fingerprints without per-frame Python loops.** Columns are hashed on up to
  four threads; fixed atom layouts are compared with array operations; the
  historical sixty-frame formula sample decides the formula fingerprint.
- **Scalar columns stay in RAM.** Admitted scalar columns, energies first, are
  kept within 128 MiB and one eighth of the budget, and saved to disk when
  disk caching is on.
- **Stable cache partitions.** Parsed batch boundaries are stored in the
  manifest, so a later, different RAM budget changes processing windows but
  not cached file names.
- **Cache columns by priority (ING-05).** Within the disk allowance keep
  (1) per-frame values, cell, PBC and species (species stored as uint8, read
  back with their parsed dtype), (2) forces, (3) positions and other per-atom
  columns. A level is kept only if its projected whole-dataset size fits;
  level 1 is always kept. One-pass loads never keep level 3, because the
  final size is unknown while scanning. Omitted columns are recorded and read
  from the source, never filled with NaN.
- **Shared parse windows for metrics.** Large uncached metric reads share one
  canonical window in RAM and serialise source parsing, so concurrent
  consumers reuse it instead of each holding a parser workspace. Small and
  random reads keep the bounded source-range path.
- **Scans do not evict their own input.** After loading, source-read misses
  do not replace persistent batches; during initial admission, atom batches
  are evicted before metadata and scalar columns.
- Existing RAM policy, admission checks and disk budgets are unchanged.

## Considered options

- **More parser threads.** Does not remove the second pass or repeated scalar
  reads.
- **Keep all parsed arrays in RAM.** Needed 7.55 GiB where about 5–6 GiB was
  available on the profiled laptop.
- **A new binary column store.** Would change cache ownership, publication
  and eviction far more; the existing batch cache serves this workload.

## Consequences

- Very large files always choose bounded storage, even when unusually large
  headers would parse into much smaller arrays.
- Partial caches trade 3D geometry hits for repeated-analysis speed. Misses
  after loading are kept in RAM only; reloading rebuilds the disk working set.
- Old manifests without stored partitions need one new metadata scan.
- Smaller files and Python-only installations keep their current reading path.

## Validation

On the profiled laptop the full file (3,856,645 frames) went from 100.6 s to
32.1 s to load and from 291.8 s to 47.8 s for Basic Errors; a fresh-server
reopen took 1.87 s and 46.9 s. These are single-laptop observations, not a
guarantee. Details: [findings report](../reports/2026-11-15-memory-saving-findings.md#indexing-beside-parsing-adr-0103)
and [SO3LR laptop profile](../reports/2026-11-15-so3lr-laptop-profile.md).
