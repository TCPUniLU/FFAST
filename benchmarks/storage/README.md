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
