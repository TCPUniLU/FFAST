# Ingestion benchmarks time synthetic files in turns

Status: Proposed 2026-11-18. Builds on ADR 0102 (memory-saving mode), ADR 0103
(one-pass indexing) and the profiler in `ffast/profiling.py`. Implements spec
`FFAST_data_ingestion_spec_v1.md` §7 as reduced by its §11a item 5.

## Problem

The next four changes (ADR 0103 follow-ups: chunked fingerprints, a SQLite
cache catalog, NumPy vs Zarr cache arrays, portable caches) each need a
yes/no answer to "is this faster, smaller or lighter than before?". The
expected effects are 5–10 %. Today FFAST cannot answer that question
reliably:

- `python -m ffast.profiling` times one real file. It runs twice (empty FFAST
  cache, then reuse) and reports one number per run. There are no repeats, no
  median, no spread.
- `benchmarks/xyz_memory.py` writes one artificial file in which every line
  is the same. That is easier to parse than real data, so it gives the wrong
  speed.
- There is no way to compare two versions of the code. Timing the old code,
  switching commits and then timing the new code mixes laptop drift (heat,
  battery mode, background indexing) into the result. A drift of 5–10 % is
  common, which is the same size as the effects we want to measure.

## Decision

Add a benchmark harness in `benchmarks/ingest/` with three commands:
`generate`, `run` and `compare`. It never runs inside FFAST itself. It adds no
code to loading, metrics or caching, so it cannot slow them down (spec §1.1).

### 1. Synthetic files

A seeded generator writes `.extxyz` files that look like MLIP validation data:
random positions in a periodic box, random species from H/C/N/O, reference
energy and forces, and one prediction (`energy_pred`, `forces_pred`) equal to
the reference plus small noise. Numbers carry 8 decimals. This is the
"standard" property profile from spec §7.2.

The matrix is 3 sizes × 3 atom-count classes × 2 count modes = 18 files:

| Axis | Values |
|---|---|
| Size | 100 MB, 1 GB, 10 GB (decimal units) |
| Atoms per frame | small 10–50, medium 50–500, large 500–10,000 |
| Count mode | constant (32, 256, 4096) or variable (uniform in the class range) |

The same seed and generator version give the same bytes on any machine and
with any number of worker processes. Each file has a manifest beside it
listing seed, version, shape, frame and atom totals, byte size and SHA-256.
Files are made when a run first needs them and are reused afterwards.
`--delete-data-after` removes each file once its case is done, because the
full matrix (about 67 GB) does not fit on the laptop.

### 2. What one run measures

A run calls the existing profiler as a subprocess,
`python -m ffast.profiling FILE --runs 2`, with fresh cache folders each time:

- **First open:** empty FFAST caches. Times loading and Basic Errors.
- **Reopen:** a second process reusing those caches. Times the same two
  stages.

The OS file cache is warm and not controlled. Results say so. A "first open"
is never called a cold disk read (spec §7.5).

Recorded per run: wall seconds and average CPU cores for loading and for
Basic Errors, peak process-tree memory, cache bytes on disk, success or
error, and the native parser's checksum.

### 3. Taking turns between versions

`run` takes one or more sides: `--side old=~/wt/old --side new=.`. A side is
a folder holding one checkout of FFAST. Each side runs with `PYTHONPATH` set
to its folder, so it imports its own `ffast` package. The runs alternate:
old, new, then new, old, and so on. Drift then affects both sides about
equally.

The compiled Rust parser is a file inside the checkout, not a Python
package. A fresh worktree has none. If a side has no compiled parser, it
uses the one from the harness's own checkout. That is refused if the two
commits differ under `rust/ffast-xyz/`, because then the old Python code
would run on a new parser. Before timing, the harness checks that each side
really imports `ffast` from its own folder.

A single side is allowed. It is equivalent to timing one commit at a time,
for when a second checkout is not practical.

### 4. Storage

Raw evidence stays as written by the profiler: `events.jsonl` and
`summary.json` per run. From each raw run folder the harness derives one row
and writes it as a small Parquet file. DuckDB queries all rows at once with
`read_parquet('rows/*.parquet')`. A `rebuild` step regenerates every row from
the raw folders, so the Parquet files are always replaceable.

DuckDB is the only new dependency, in a new optional extra `bench`. DuckDB
reads and writes Parquet by itself, so pyarrow is not needed.

### 5. Comparison

`compare` groups rows by case, scenario and measure (load seconds, Basic
Errors seconds, peak memory, cache bytes). For each side it reports run
count, median, 25th and 75th percentiles and p95. It then gives the
candidate/baseline median ratio and one of three verdicts:

- **worse:** ratio above 1 + alarm, *and* the candidate's 25th percentile is
  above the baseline's 75th;
- **better:** the mirror image;
- **no clear change:** anything else.

The alarms are the spec §8 values: 10 % for time, peak memory and cache size.
The parsing-only 5 % alarm does not apply, because parsing alone is not
timed. With 5 runs, p95 is close to the maximum. It is reported but never
used for a verdict.

`compare` refuses to compare when the two sides used different dataset
checksums or, across run sets, different hosts.

## Considered and rejected

- **Time one commit, switch, time the other.** Simpler, but laptop drift
  between the two hours appears as a code difference. Kept only as the
  single-side case.
- **Parquet only, no raw JSON.** Loses the per-span evidence the spec asks to
  publish and that diagnosis needs.
- **One atom-count shape.** Cheaper, but chunked fingerprints and Zarr
  chunking behave differently for 30-atom and 4,000-atom frames.
- **Parse-only timing in this step.** A third code path. The fast-parser
  measurements in ADR 0100 already cover it. It can be added later as a
  scenario.
- **pytest-benchmark.** Suited to small in-process functions, not to
  subprocesses lasting minutes with their own cache folders.

## Consequences

- Every later step in the spec order quotes `compare` output instead of single
  timings.
- The full matrix needs about 67 GB of source files plus room for one case's
  caches. The laptop had 89 GiB free on 2026-11-18. With less room, use
  `--delete-data-after` or MeluXina scratch.
- A full matrix with 2 sides × 5 repeats takes hours. `--sizes`, `--classes`
  and `--counts` select a subset.
- Cold-disk timing, browser first-plot timing and worker-count scaling remain
  out of scope (spec §11a items 1 and 4).
