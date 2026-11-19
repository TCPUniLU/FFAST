"""One row per (run, variant, file, warm/cold), stored as Parquet (ADR 0109 §4).

Same approach as ``benchmarks/ingest/rows.py``: the raw JSON of each run stays beside
the rows, and the rows are derived from it with every column typed explicitly.
"""

import json
from pathlib import Path

from benchmarks.ingest.rows import _quote, host_facts  # noqa: F401  (host_facts re-exported)

COLUMNS = {
    "run_set": "VARCHAR", "case": "VARCHAR", "real": "BOOLEAN", "variant": "VARCHAR",
    "repeat": "INTEGER", "order_index": "INTEGER", "cache": "VARCHAR",
    "completed": "BOOLEAN", "error": "VARCHAR",
    "write_s": "DOUBLE", "write_cpu_s": "DOUBLE", "scan_s": "DOUBLE", "scan_cpu_s": "DOUBLE",
    "frame_p50_ms": "DOUBLE", "frame_p95_ms": "DOUBLE", "frames_total_s": "DOUBLE",
    "reopen_s": "DOUBLE", "disk_bytes": "BIGINT", "disk_alloc_bytes": "BIGINT",
    "n_files": "BIGINT", "peak_rss_bytes": "BIGINT",
    "rss_write": "BIGINT", "rss_scan": "BIGINT", "rss_frames": "BIGINT", "rss_reopen": "BIGINT",
    "source_bytes": "BIGINT", "frames": "BIGINT", "atoms": "BIGINT",
    "blosc_nthreads": "INTEGER", "started_utc": "VARCHAR",
    "host": "VARCHAR", "cpu_model": "VARCHAR", "ram_bytes": "BIGINT", "os": "VARCHAR",
    "python": "VARCHAR",
}
OPS = ("write", "check", "scan", "frames", "reopen")
MEASURE_KEYS = ("write_s", "write_cpu_s", "scan_s", "scan_cpu_s", "frame_p50_ms", "frame_p95_ms",
                "frames_total_s", "reopen_s", "disk_bytes", "disk_alloc_bytes", "n_files")


def row_from_run(run_dir, facts=None):
    """One row from the raw run folder ``run_dir`` (``harness.json`` and one JSON per operation)."""
    run_dir = Path(run_dir)
    facts = facts or {}
    harness = json.loads((run_dir / "harness.json").read_text())
    ops = {op: json.loads((run_dir / f"{op}.json").read_text())
           for op in OPS if (run_dir / f"{op}.json").is_file()}
    error = harness.get("error")
    check = ops.get("check")
    if error is None and check is not None and not check["ok"]:
        error = "not bit-identical: " + "; ".join(check["bad"][:3])
    needed = ("write", "scan", "frames", "reopen")
    if error is None and not all(op in ops for op in needed):
        error = "an operation did not finish"
    row = {c: None for c in COLUMNS}
    row.update({k: harness.get(k) for k in ("run_set", "case", "real", "variant", "repeat",
                                             "order_index", "cache", "source_bytes", "frames",
                                             "atoms", "started_utc")})
    row.update(completed=error is None, error=error, **{k: facts.get(k) for k in
                                                        ("host", "cpu_model", "ram_bytes", "os", "python")})
    for op in ops.values():
        for key in MEASURE_KEYS:
            if key in op:
                row[key] = op[key]
    for op in ("write", "scan", "frames", "reopen"):
        if op in ops:
            row[f"rss_{op}"] = ops[op]["peak_rss_bytes"]
    peaks = [row[f"rss_{op}"] for op in ("write", "scan", "frames", "reopen") if row[f"rss_{op}"]]
    row["peak_rss_bytes"] = max(peaks) if peaks else None
    row["blosc_nthreads"] = next((o["blosc_nthreads"] for o in ops.values() if "blosc_nthreads" in o), None)
    return row


def stem_of(row):
    return f"{row['case']}__{row['variant']}__r{row['repeat']}__{row['cache']}"


def write_row(row, rows_dir):
    """Write ``rows_dir/<stem>.parquet`` with every column typed explicitly."""
    import duckdb

    rows_dir = Path(rows_dir)
    rows_dir.mkdir(parents=True, exist_ok=True)
    stem = stem_of(row)
    source = rows_dir / f"{stem}.jsonl.tmp"
    target = rows_dir / f"{stem}.parquet"
    partial = rows_dir / f"{stem}.parquet.tmp"
    source.write_text(json.dumps({c: row.get(c) for c in COLUMNS}, allow_nan=False) + "\n")
    columns = ", ".join(f"'{c}': '{t}'" for c, t in COLUMNS.items())
    try:
        duckdb.sql(
            f"COPY (SELECT * FROM read_json('{_quote(source)}', format='newline_delimited', "
            f"columns={{{columns}}})) TO '{_quote(partial)}' (FORMAT parquet)")
        partial.replace(target)
    finally:
        source.unlink(missing_ok=True)
        partial.unlink(missing_ok=True)
    return target
