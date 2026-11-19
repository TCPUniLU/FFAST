# Storage bench: NumPy batches against Zarr

Times FFAST's parsed-cache batches (`.npz`, read in place) against a Zarr sweep on the
same arrays. Design: ADR 0109. Run all commands from the repository root.

Install once: `pip install -e ".[bench]"` (DuckDB and `zarr` 3.x).

## Commands

```
# 1. load a file once through FFAST and keep the batch set it writes
python -m benchmarks.storage source temp/bench-data/1GB-medium-variable-s0-g1.extxyz \
  --out temp/storage-bench/src-1gb-medium-variable [--memory-bytes 3G] [--real] [--label NAME] \
  [--profiler-arg=--pred-force-key=forces_pred ...]
```

`source` runs `python -m ffast.profiling` once. FFAST decides by its memory budget
whether a file is stored as whole arrays or as batches; when the automatic mode gives
whole arrays, `source` loads again in memory-saving mode, so the batches exist. Which
mode was used is written to `source.json` with the batch list, the column names,
dtypes and shapes, and the row and byte totals. `--out` must be new or empty.

```
# slice the first whole frames of an .extxyz file (cut at a frame boundary, checked with FFAST's frame scan)
python -m benchmarks.storage slice /path/to/big.extxyz --bytes 1GB --out temp/storage-bench/big-1gb.extxyz

# time the variants; each operation runs in a fresh process, variants take turns
python -m benchmarks.storage run --source temp/storage-bench/src-1gb-medium-variable \
  --source temp/storage-bench/src-1gb-medium-constant --variants all --repeats 5 \
  --output temp/storage-bench/round1 [--cold] [--keep-stores] [--store-dir temp/storage-bench/stores]

# medians, quartiles, p95, ratio to npz, verdicts, rule-outs and the go/no-go of ADR 0109 section 5
python -m benchmarks.storage report temp/storage-bench/round1 [--baseline npz]
```

`--output` must not exist yet. Variant names: `npz` and `zarr-<codec>-<chunk>` with codec
`none`, `lz4`, `zstd1`, `zstd3`, `zstd5` and chunk `16k` (16,384 rows, in shards of 64
chunks), `128k` (131,072) or `1m` (1,048,576): 16 variants. `npz-copy` is the same code as
`npz` under another name, for an A/A check. A variant that is not bit-identical to the
source is not timed.

## What one run does

For each source and repeat, each variant in turn: write a fresh store (reading a window of
the source is not timed; writing and syncing it is), check it against the source, then
`scan` (every `forces:` column, window by window), `frames` (2,000 random frames, fixed seed,
positions and every `forces:` column), `reopen` (open the store, read frame 0), each in its
own process. The store is deleted afterwards. Blosc and Zarr's own codec pool run on one
thread; the value that took effect is stored in the rows (`blosc_nthreads`).

Warm (default): the store is read once before each timed operation. Cold (`--cold`): the
file cache is dropped with `sudo -n purge` before each timed operation. macOS asks for a
password, so run `sudo -v` in the same terminal first; `run --cold` refuses to start when
`sudo -n true` fails.

## What is stored

- `raw/<case>/<variant>/r<N>/`: one JSON per operation (`write`, `check`, `scan`, `frames`,
  `reopen`) and `harness.json`.
- `rows/*.parquet`: one row per run, derived from the raw folder; query with DuckDB.
- `run_set.json`: host, `zarr` and `numcodecs` versions, arguments.
- `report` writes `report.md` into the run set.

## Disk

A store is about the size of its source batches. Variant stores live under
`temp/storage-bench/stores` and are deleted after their runs. `run` stops when the free disk
is under 1.3 times the source's batch bytes.
