"""One row per scenario of a run, stored as Parquet and queried with DuckDB (ADR 0105 §4)."""

import json
import platform
import shutil
import subprocess
from pathlib import Path

COLUMNS = {
    "run_set": "VARCHAR", "side": "VARCHAR", "commit": "VARCHAR", "dirty": "BOOLEAN",
    "native_from": "VARCHAR", "case": "VARCHAR", "size": "VARCHAR", "atoms": "VARCHAR",
    "counts": "VARCHAR", "file_bytes": "BIGINT", "frames": "BIGINT",
    "atoms_total": "BIGINT", "dataset_sha256": "VARCHAR", "repeat": "INTEGER",
    "order_index": "INTEGER", "scenario": "VARCHAR", "load_s": "DOUBLE",
    "load_cores": "DOUBLE", "basic_errors_s": "DOUBLE", "basic_errors_cores": "DOUBLE",
    "peak_rss_bytes": "BIGINT", "ram_budget_bytes": "BIGINT", "completed": "BOOLEAN", "error": "VARCHAR",
    "native_sha256": "VARCHAR", "cache_bytes": "BIGINT", "started_utc": "VARCHAR",
    "host": "VARCHAR", "cpu_model": "VARCHAR", "ram_bytes": "BIGINT",
    "os": "VARCHAR", "python": "VARCHAR",
}
SCENARIOS = (("first_open", "run-1"), ("reopen", "run-2"))


def host_facts():
    """What identifies the machine; written once into ``run_set.json``."""
    import psutil

    cpu = platform.processor()
    try:
        if platform.system() == "Darwin":
            cpu = subprocess.run(["sysctl", "-n", "machdep.cpu.brand_string"],
                                 capture_output=True, text=True).stdout.strip() or cpu
        elif platform.system() == "Linux":
            for line in Path("/proc/cpuinfo").read_text().splitlines():
                if line.startswith("model name"):
                    cpu = line.split(":", 1)[1].strip()
                    break
    except OSError:
        pass
    return {"host": platform.node(), "cpu_model": cpu,
            "ram_bytes": psutil.virtual_memory().total, "os": platform.platform(),
            "python": platform.python_version()}


def _spans(path):
    """The load and Basic Errors spans of one run, read line by line (the file can be big)."""
    found = {}
    if not path.is_file():
        return found
    with path.open() as stream:
        for line in stream:
            for name in ("workload.load", "workload.basic_errors"):
                if f'"name": "{name}"' in line:
                    try:
                        event = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if event.get("name") == name:
                        found[name] = event
    return found


def _why_not(folder, name, harness):
    """The reason a run has no summary: its status file, or the end of its log."""
    status = folder.parent / f"{folder.name}-status.json"
    reason = f"{name} did not finish (profiler exit code {harness.get('exit_code')})"
    if status.is_file():
        state = json.loads(status.read_text()).get("state")
        reason = f"{name} {state}"
    log = folder.parent / f"{folder.name}.log"
    if log.is_file():
        lines = [x for x in log.read_text(errors="replace").splitlines() if x.strip()]
        if lines:
            reason += ": " + lines[-1][:300]
    return reason


def rows_from_run(out_dir, run_set=None):
    """Two rows (first open, reopen) from one raw run folder."""
    out_dir = Path(out_dir)
    harness = json.loads((out_dir / "harness.json").read_text())
    manifest = harness.get("manifest") or {}
    run_set = run_set or {}
    rows = []
    for scenario, name in SCENARIOS:
        folder = out_dir / name
        summary_path = folder / "summary.json"
        summary = json.loads(summary_path.read_text()) if summary_path.is_file() else None
        meta = (summary or {}).get("metadata", {})
        spans = _spans(folder / "events.jsonl")
        load, errors = spans.get("workload.load"), spans.get("workload.basic_errors")
        completed = bool(summary and summary.get("completed") and load and errors
                         and not load.get("failed") and not errors.get("failed"))
        if summary is None:
            error = _why_not(folder, name, harness)
        elif summary.get("error"):
            error = summary["error"]
        elif not completed:
            error = "load or Basic Errors span missing"
        else:
            error = None
        native = meta.get("native_parser") or {}
        rows.append({
            "run_set": harness.get("run_set"), "side": harness.get("side"),
            "commit": harness.get("commit"), "dirty": harness.get("dirty"),
            "native_from": harness.get("native_from"), "case": harness.get("case"),
            "size": manifest.get("size"), "atoms": manifest.get("atoms"),
            "counts": manifest.get("counts"), "file_bytes": manifest.get("bytes"),
            "frames": manifest.get("frames"), "atoms_total": manifest.get("atoms_total"),
            "dataset_sha256": manifest.get("sha256"), "repeat": harness.get("repeat"),
            "order_index": harness.get("order_index"), "scenario": scenario,
            "load_s": load and load["seconds"],
            "load_cores": load and load.get("average_process_cores"),
            "basic_errors_s": errors and errors["seconds"],
            "basic_errors_cores": errors and errors.get("average_process_cores"),
            "peak_rss_bytes": (summary or {}).get("peak_process_tree_rss_bytes"),
            "ram_budget_bytes": meta.get("ram_budget_bytes"),
            "completed": completed, "error": error,
            "native_sha256": native.get("sha256"), "cache_bytes": harness.get("cache_bytes"),
            "started_utc": meta.get("started_utc"),
            "host": run_set.get("host"), "cpu_model": run_set.get("cpu_model"),
            "ram_bytes": meta.get("ram_bytes") or run_set.get("ram_bytes"),
            "os": meta.get("platform") or run_set.get("os"),
            "python": (meta.get("python") or run_set.get("python") or "").split(" ")[0] or None,
        })
    return rows


def write_rows(rows, rows_dir, stem):
    """Write ``rows`` to ``rows_dir/<stem>.parquet`` with every column typed explicitly."""
    import duckdb

    rows_dir = Path(rows_dir)
    rows_dir.mkdir(parents=True, exist_ok=True)
    source = rows_dir / f"{stem}.jsonl.tmp"
    target = rows_dir / f"{stem}.parquet"
    partial = rows_dir / f"{stem}.parquet.tmp"
    source.write_text("".join(
        json.dumps({c: row.get(c) for c in COLUMNS}, allow_nan=False) + "\n" for row in rows))
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


def _quote(path):
    return str(path).replace("'", "''")


def stem_of(harness):
    return f"{harness['case']}__{harness['side']}__r{harness['repeat']}"


def rebuild(output):
    """Delete ``rows/`` and make it again from every raw run folder."""
    output = Path(output)
    run_set_path = output / "run_set.json"
    run_set = json.loads(run_set_path.read_text()) if run_set_path.is_file() else {}
    rows_dir = output / "rows"
    shutil.rmtree(rows_dir, ignore_errors=True)
    count = 0
    for path in sorted((output / "raw").glob("*/*/r*/harness.json")):
        harness = json.loads(path.read_text())
        write_rows(rows_from_run(path.parent, run_set.get("host_facts", run_set)),
                   rows_dir, stem_of(harness))
        count += 1
    print(f"rebuilt {count} run files in {rows_dir}")
    return count
