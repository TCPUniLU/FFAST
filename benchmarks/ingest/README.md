# Ingestion benchmarks

Times FFAST loading and Basic Errors on seeded synthetic `.extxyz` files, and
compares two checkouts of the code by taking turns. Design: ADR 0105.

Install DuckDB once: `pip install -e ".[bench]"`.

## Commands

Run all of them from the repository root.

```
# one file (made on first use, reused afterwards)
python -m benchmarks.ingest generate --size 100MB --atoms small --counts variable --data-dir temp/bench-data

# time one or more checkouts; sides take turns (old, new, new, old, ...)
python -m benchmarks.ingest run --side old=~/wt/old --side new=. \
  --sizes 100MB,1GB --classes small,medium,large --counts constant,variable \
  --repeats 5 --data-dir temp/bench-data --output temp/bench/2026-11-18-a1 \
  [--timeout 1800] [--delete-data-after] [--workers 4]

# medians, spread and a verdict per case, scenario and measure
python -m benchmarks.ingest compare temp/bench/2026-11-18-a1 --baseline old --candidate new \
  [--with temp/bench/other-run-set]

# regenerate rows/ from the raw run folders
python -m benchmarks.ingest rebuild temp/bench/2026-11-18-a1
```

`--output` must not exist yet. A single `--side` is allowed (one commit at a
time); put the other commit in a second run set and join them with `compare --with`.
`compare` refuses data with different checksums and run sets from different machines.

## The baseline checkout

```
git worktree add ~/wt/old <commit>
```

A fresh worktree has no compiled parser. The harness then uses the parser of its
own checkout, but only if the two commits have the same `rust/ffast-xyz/src` and
`python` files. Otherwise build it in the worktree first:
`cd ~/wt/old/rust/ffast-xyz && maturin develop --release`. Remove the worktree
afterwards with `git worktree remove ~/wt/old`.

Before timing, each side must import `ffast` from its own folder. The venv's
editable install points at the main checkout, so the harness sets `PYTHONPATH`
and checks the result.

## What is stored

- `raw/<case>/<side>/r<N>/`: the profiler's own `events.jsonl` and `summary.json`
  for `run-1` (first open) and `run-2` (reopen), plus `harness.json`. The FFAST
  cache folder is measured and deleted after each run.
- `rows/*.parquet`: two rows per run, derived from the raw folders. Query them
  with `duckdb.sql("SELECT ... FROM read_parquet('rows/*.parquet')")`.
- `run_set.json`: host, CPU, sides with commits, arguments.

## Disk

Source files are 0.1, 1 and 10 GB (a bit over). The full matrix of 18 files is
about 67 GB, and one case's caches need roughly the file size again. Use
`--delete-data-after` or a subset (`--sizes`, `--classes`, `--counts`) when the
disk is small. Generation refuses to start with less than 1.2 × the file size free.
Making a 100 MB file takes about a second with 4 workers.

## Memory budget

Every run gets the same FFAST memory budget, `--memory-bytes` (default 3G), set
through `FFAST_MEMORY_BYTES`. Without it FFAST takes 80 % of the RAM free at
start. On 2026-11-18 that was 3.3–5.1 GiB from run to run on one 10 GB file, and
FFAST then split the file into 58–75 batches: the cache was 4.7 or 7.0 GB and
first-open loading 15 or 19 s, on identical code. FFAST never takes more than the
RAM that is free, so a budget above it is cut down and varies again: `run` warns
when that happens, and `compare` lists the budgets each side got.
`--memory-bytes 0` lets FFAST choose, to time its default behaviour.

## Caveats

- The OS file cache is warm and not controlled. "First open" means empty FFAST
  caches, not a cold disk read.
- `cache_bytes` is measured once after both runs, so the first-open and reopen
  rows of one run carry the same value.
- With 5 runs p95 is almost the maximum. It is shown, never used for a verdict.
- A verdict needs a ratio past 10 % *and* non-overlapping interquartile ranges.
  Same code on both sides should read "no clear change" everywhere; if not, the
  machine is noisy, not the code different.
