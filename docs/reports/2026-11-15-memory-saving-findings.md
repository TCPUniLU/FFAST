# Memory-saving mode: findings during implementation, 2026-11-15

Evidence behind [ADR 0102](../adr/0102-large-datasets-have-a-memory-saving-mode.md)
and [ADR 0103](../adr/0103-large-xyz-files-are-indexed-beside-parsing.md).
The ADRs record the decisions; this report keeps the constraints, incidents
and measurements that led to them. Full-file laptop timings are in the
[SO3LR laptop profile](2026-11-15-so3lr-laptop-profile.md).

## Constraints found before implementation

This is a storage and consumer change, not just a replacement for the load
guard. `ReaderDataset` stored joined arrays. Several metrics, model
inputs and subset accessors obtain whole-dataset arrays before selecting or
reducing. The existing parsed-array disk cache was created after a cold load
has built the full arrays in RAM.

The current dataset fingerprint includes parsed elements, positions, energies
and forces, in a defined order and with their existing dtypes. Establishing
that fingerprint requires reading the relevant values across the dataset,
even if the values are not retained in RAM. Incremental hashing exists.

### Existing fast parser and cluster I/O evidence

Keep the optional Rust fast parser as the parsing path. The storage change
must not replace it with a slower parser. Commit `9dfef2db` replaced mapped
cold XYZ input with buffered `pread` on Lustre: finding frames in `shard_00`
took 19.7 s mapped versus 1.6 s with plain reads. Commit `e88c058b` applied
plain reads to energy extraction and removed the `memmap2` dependency.
Commit `2b905ac1` then overlapped each task's reading and parsing; the
recorded 32 GB first open was 31.7–32.3 s.

These measurements concern mapping the original text input. They do not
establish the performance of mapping parsed binary arrays, which requires a
separate benchmark. A memory-mapped binary store is not a settled choice.

### General-purpose systems considered

[Arrow IPC](https://arrow.apache.org/docs/python/ipc.html) supports indexed
record batches with ordinary file I/O, and its
[Rust reader](https://arrow.apache.org/rust/arrow_ipc/reader/struct.FileReader.html)
supports buffered `Read + Seek` inputs.
[DuckDB](https://duckdb.org/2024/07/09/memory-management) combines streaming,
a shared buffer manager and intermediate spill to disk.
[Polars](https://docs.pola.rs/user-guide/concepts/streaming/) offers streaming
query execution, while
[Dask arrays](https://docs.dask.org/en/latest/array-best-practices.html)
execute numerical work in chunks. These are candidates and architectural
examples, not adopted dependencies; none by itself makes existing arbitrary
FFAST consumers respect the agreed shared memory budget.

## First validation

A local warm-file check loaded 50,000 frames / two million atoms (107 MiB of
parsed core arrays) with an 8 MiB managed budget. Fingerprints and streamed
results matched the reference; sampled process RSS grew by about 15–17 MiB.
Reproduce locally or inside a Slurm allocation with
`python benchmarks/xyz_memory.py`. This checks bounded behavior, not cluster
disk throughput. Slurm/Lustre random and sequential access, concurrent jobs
and production model workspaces still need measurement.

## Workstation RAM share: 60% to 80%

The workstation share increased from 60% to 80% at the user's request on
2026-11-15. The same-cache full SO3LR comparison improved Basic Errors from
27.51 to 23.19 seconds, with larger native parser batches and unchanged
four-batch concurrency. The 512 MiB floor, live headroom checks, explicit
overrides and 85% cluster policy were kept. This changed the initial
fraction; it did not add automatic growth of a live budget.

## Incidents on the first real 84 GiB biomolecule trajectory

### NaN atom values sent every batch to the Python parser

An optional `hirshfeld_ratios` column contained `nan` throughout. Rust
rejected each batch and the Python parser consumed about 90% of the profiled
load time, despite the per-attempt fast-reader log. Rust now accepts standard
signed, case-insensitive NaN/infinity atom values with byte-for-byte Python
parity, and indexed reads suppress repetitive per-batch fast-attempt
messages. On 10,000 actual frames (206 MiB), the bounded scan fell from
3.37 s to 0.68 s with identical column dtypes, missing-value metadata and
fingerprints. This measures a warm prefix, not the full file.

### Prediction attachment rescanned the file

Prediction fingerprints now use known column digests; reshaping preserves
those digests, and float-normalized integer energies use the floating digest
already computed during the scan. A later same-file prediction import reuses
the live indexed store and its selected frames. Exact matching element and
position digests avoid repeated compatibility scans; source-change checks
still run. Regression tests attach both in-file and later same-file
predictions with all data-read paths forbidden and verify the historical
prediction fingerprint. The actual 10,000-frame prefix attached each way in
under 1 ms with zero source or parsed-cache reads; full-file attachment time
has not been measured.

### Atom colouring stored a 10.6 GiB intermediate

Full-dataset atom colouring stored force difference as a complete 10.6 GiB
intermediate before its scalar-per-atom reduction. With 17 GiB free disk,
the spill headroom policy refused that intermediate, and the RAM fallback
exceeded the 2.7 GiB budget. Streaming execution now keeps dependency arrays
virtual and passes bounded batches to their consumers; final outputs still
need to fit RAM or result storage. A cached virtual dependency requested as a
root is admitted again as a full output. Tests cover reductions with result
spill disabled and both frame and atom outputs, the unchanged values, and
root admission after dependency reuse. The exact colouring in the screenshot
also matched direct calculation over 1,126,046 atoms from the original file
with result spill disabled. Storage failures now retain both the disk refusal
and the RAM refusal instead of hiding the disk reason.

For 3D colouring, even storing the reduced per-atom output was unnecessary.
The follow-up stores exact min/max and a lazy recipe for visible-frame values
in a separate small colour cache. The background range scan never spills
colour arrays. Expressions validate non-finite values per batch and retain
masked-value warnings and no-value gaps. The range store shares the existing
lifecycle for coalescing, cancellation and invalidation; full plot results
use their normal result store and remain independently admitted. Already
computed plot results can be reused by 3D views.

Tests forbid result-file creation, verify reuse without another source scan,
preserve frame values and masked-value semantics, and exercise the scene's
background completion and dataset deletion. A 10,000-frame prefix of the
actual file produced bounds for 1,126,046 atoms in 1.93 s with an 8 MiB
managed budget and disk spill disabled; its 8.59 MiB colour output remained
virtual. The next range request reused the summary in 0.4 ms. Finding exact
bounds over the full file still requires reading relevant values.

### A ten-metric dashboard exhausted the shared budget

Each uncached storage batch reserved about 790 MiB for parsing, and streaming
steps bypassed the normal full-array scheduling gate. Several simultaneous
scans exhausted the shared budget. Metric source reads now subdivide storage
batches by parser workspace, including batches described by existing
manifests. Lazy operations preserve their input batch limits when a vector
becomes a scalar or boolean, and streamed computation, final result admission
and colour range scans share a memory-aware scheduling gate. Busy scans wait
with the existing cancellation checks. Initial sequential indexing keeps its
larger storage batches. Tests reproduce the failure without the gate and run
the ten reported metrics together within an 8 MiB managed budget. The same
ten metrics also passed over the original file's 10,000-frame /
1,126,046-atom prefix with disk spill disabled, and force MAE/RMSE matched
independent bounded reductions. This validates memory behaviour on a prefix,
not full-file performance.

## Indexing beside parsing (ADR 0103)

On an 18 GiB, eleven-core laptop, a 16.24 GB SO3LR file took 100.6 seconds
to load and 291.8 seconds to calculate Basic Errors. A new server with the
saved dataset manifest loaded in 1.8 seconds but spent 513.6 seconds on the
same metrics. The target was under sixty seconds for each stage. The
full-file profile found two physical passes during loading, repeated source
parsing during metrics, cache partitions dependent on the current RAM budget,
and large scans evicting the files they would need on the next pass.

After the change, the same full file, retaining all 3,856,645 frames,
measured 32.1 seconds for the first load and 47.8 seconds for Basic Errors
with default laptop limits. The fresh-server reopen measured 1.87 seconds for
loading and 46.9 seconds for metrics. All fourteen requests succeeded and the
dataset fingerprint matched the original profile. These are observational
laptop measurements, not a guarantee for every drive or memory condition.
Keeping all parsed arrays would have required 7.55 GiB while only about
5–6 GiB was available to the laptop process and other applications.
