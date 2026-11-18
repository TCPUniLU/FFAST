# Large datasets have a memory-saving mode

Status: Accepted and implemented 2026-11-15. Cluster benchmarking pending.
Supersedes eager-only XYZ storage in ADR 0076.

A cold load used to be refused when its parsed arrays and working memory
exceeded the memory budget (ADR 0083). The frame index already locates every
XYZ frame, so holding every parsed frame in RAM is not needed to use a large
dataset. Large XYZ datasets therefore get a memory-saving mode: keep the frame
index, read frames and batches on demand, and bound what stays in RAM.

## Decision

- **Two storage modes behind one dataset interface.** Datasets whose parsed
  arrays and working memory fit keep the fast full-array path. Larger ones use
  memory-saving mode, chosen automatically from the parsed-size estimate; users
  can force it for smaller files. Only `.xyz`/`.extxyz` for now; other formats
  keep their path.
- **Full data, never silent sampling.** Viewing, analysis, prediction and
  export cover the whole selected dataset, in batches where needed. Slower
  processing is accepted; dropping frames is not.
- **One shared RAM budget per server.** All datasets, cached batches and
  active processing reserve from it. Optional caches are evicted before a
  reservation is refused. Opening more datasets does not multiply limits.
- **Different automatic policies for laptops and Slurm.** A workstation admits
  80% of available RAM; a cgroup/Slurm job admits 85% of its remaining
  allocation, never the node's free memory. Both keep at least 512 MiB
  headroom. A Slurm job whose allocation is not visible (e.g. `--mem=0`)
  falls back to the workstation rule with a warning; `FFAST_MEMORY_BYTES` or
  `resources.ram_bytes` sets it explicitly.
- **Optional disk caches with their own budgets.** Parsed batches (uncompressed
  `.npz`) and large results (`.npy`) each default to 20 GiB. Entries are
  published atomically. If a cache cannot be written, the dataset still works
  from the original file: the cache accelerates, it is never required.
- **Existing fingerprints are kept**, including dtypes and byte order. The
  first open reads the relevant data once to compute them, in bounded memory.
- **Consumers stream where they can.** Built-in reductions, differences,
  smoothing, histograms, prediction inputs, dataset views, plot thinning and
  XYZ export take bounded batches. Intermediate arrays stay virtual; only
  final outputs are admitted to RAM or result storage. 3D colouring keeps only
  exact min/max and computes visible-frame values on demand.
- **Operations that cannot stream are checked, not assumed.** If an
  operation's working memory does not fit, it is refused with a reason and the
  dataset stays open. Whole-result CSV/RPC replies keep their bulk protocol.
- **Source changes stop reads.** A changed file is detected and a reload is
  requested; frames from different versions are never combined. Following a
  growing file is a separate feature.
- **Cancellation between batches** discards unfinished writes, keeps valid
  cache entries and releases working memory.

## Considered options

- **Raise the load limit.** Rejected: risks swapping or the process being
  killed.
- **Require a complete parsed copy on disk.** Bounds memory, but excludes
  datasets larger than free cache space. Reading the original file stays the
  fallback instead.
- **Adopt Arrow IPC, DuckDB, Polars or Dask.** None of them makes existing
  FFAST consumers respect one shared memory budget, and each adds a
  dependency. NumPy files serve the current need; Arrow remains a candidate.

## Consequences

- The first open is not instant: it reads the relevant data to establish the
  fingerprint. Random single-frame reads do not get sequential-disk speed.
- An arbitrary external metric or model with a large working set is not made
  streamable; it is refused when it cannot fit.
- Budget defaults are initial policies, not measured cluster optima. Process
  RSS also includes Python, allocator retention, index metadata and external
  libraries, which the budget does not manage.
- The batch interface can serve future HDF5 or TFDS readers; none is included.

## Validation

Regression tests cover fingerprints and dtypes against the eager path,
single-frame parsing, warm cache slices without whole-batch loads, source
changes, reductions and the metric graph, batched prediction, cancellation
and operation refusal. A 50,000-frame file loaded within an 8 MiB managed
budget with matching fingerprints (`python benchmarks/xyz_memory.py`).
Constraints, incidents on a real 84 GiB trajectory and measurements are in the
[findings report](../reports/2026-11-15-memory-saving-findings.md).
Configuration: [usage guide](../usage.md#large-xyz-files).
