"""Rows of the ingestion benchmark: raw run folder to Parquet and back (ADR 0105 §4)."""

import json

import pytest

duckdb = pytest.importorskip("duckdb")

from benchmarks.ingest.rows import COLUMNS, rebuild, rows_from_run, stem_of, write_rows

MANIFEST = {"size": "100MB", "atoms": "small", "counts": "constant", "bytes": 1000,
            "frames": 10, "atoms_total": 320, "sha256": "abc"}
RUN_SET = {"host": "box", "cpu_model": "Fake CPU", "ram_bytes": 8, "os": "TestOS",
           "python": "3.11.0"}


def _span(name, seconds, cores):
    return json.dumps({"type": "span", "name": name, "phase": name.split(".")[1],
                       "seconds": seconds, "average_process_cores": cores, "failed": False})


def _run(folder, name, load, errors, completed=True, error=None):
    folder = folder / name
    folder.mkdir()
    (folder / "events.jsonl").write_text("\n".join([
        json.dumps({"type": "metadata"}),
        _span("workload.load", load, 2.0),
        _span("workload.basic_errors", errors, 1.5),
        _span("workload.basic_errors_cached", 0.001, 1.0),
    ]) + "\n")
    (folder / "summary.json").write_text(json.dumps({
        "completed": completed, "error": error, "peak_process_tree_rss_bytes": 5000,
        "metadata": {"started_utc": "2026-11-18T10:00:00+00:00", "ram_bytes": 16,
                     "platform": "macOS-x", "python": "3.11.14 (main)",
                     "native_parser": {"sha256": "nat"}, "ram_budget_bytes": 4096}}))


def _folder(tmp_path, with_run_2=True):
    out = tmp_path / "raw" / "case-a" / "old" / "r2"
    out.mkdir(parents=True)
    _run(out, "run-1", 12.5, 20.25)
    if with_run_2:
        _run(out, "run-2", 2.5, 4.0)
    (out / "harness.json").write_text(json.dumps({
        "run_set": "set1", "side": "old", "commit": "deadbeef", "dirty": False,
        "native_from": "side", "case": "case-a", "manifest": MANIFEST, "repeat": 2,
        "order_index": 1, "exit_code": 0 if with_run_2 else 1, "cache_bytes": 777}))
    return out


def test_two_rows_with_the_values_of_each_run(tmp_path):
    first, reopen = rows_from_run(_folder(tmp_path), RUN_SET)
    assert (first["scenario"], reopen["scenario"]) == ("first_open", "reopen")
    assert (first["load_s"], first["basic_errors_s"]) == (12.5, 20.25)
    assert (reopen["load_s"], reopen["basic_errors_s"]) == (2.5, 4.0)
    assert first["load_cores"] == 2.0 and first["basic_errors_cores"] == 1.5
    assert first["peak_rss_bytes"] == 5000 and first["native_sha256"] == "nat"
    assert first["ram_budget_bytes"] == 4096
    assert first["cache_bytes"] == reopen["cache_bytes"] == 777
    assert first["completed"] and first["error"] is None
    assert first["host"] == "box" and first["cpu_model"] == "Fake CPU"
    assert first["python"] == "3.11.14" and first["dataset_sha256"] == "abc"
    assert first["repeat"] == 2 and first["side"] == "old" and first["file_bytes"] == 1000


def test_a_missing_run_2_gives_an_incomplete_row_with_its_error(tmp_path):
    out = _folder(tmp_path, with_run_2=False)
    (out / "run-1.log").write_text("starting\nRuntimeError: it broke\n")
    (out / "run-1-status.json").write_text(json.dumps({"state": "failed"}))
    first, reopen = rows_from_run(out, RUN_SET)
    assert first["completed"]
    assert not reopen["completed"] and reopen["load_s"] is None
    assert "run-2" in reopen["error"]


def test_rows_round_trip_through_parquet(tmp_path):
    rows = rows_from_run(_folder(tmp_path), RUN_SET)
    path = write_rows(rows, tmp_path / "rows", "case-a__old__r2")
    got = duckdb.sql(
        f"SELECT scenario, load_s, completed, dirty, error FROM read_parquet('{path}') "
        "ORDER BY scenario").fetchall()
    assert got == [("first_open", 12.5, True, False, None), ("reopen", 2.5, True, False, None)]


def test_an_all_null_column_keeps_its_type(tmp_path):
    out = _folder(tmp_path, with_run_2=False)
    rows = rows_from_run(out, RUN_SET)
    path = write_rows(rows[1:], tmp_path / "rows", "failed")
    types = dict(duckdb.sql(f"DESCRIBE SELECT * FROM read_parquet('{path}')").fetchall()[i][:2]
                 for i in range(len(COLUMNS)))
    assert types["load_s"] == "DOUBLE" and types["peak_rss_bytes"] == "BIGINT"
    assert types["completed"] == "BOOLEAN"


def test_rebuild_regenerates_every_row_file(tmp_path):
    out = _folder(tmp_path).parents[3]
    (out / "run_set.json").write_text(json.dumps({"host_facts": RUN_SET}))
    (out / "rows").mkdir()
    (out / "rows" / "stale.parquet").write_text("junk")
    assert rebuild(out) == 1
    names = sorted(p.name for p in (out / "rows").iterdir())
    assert names == ["case-a__old__r2.parquet"]
    n = duckdb.sql(f"SELECT count(*) FROM read_parquet('{out}/rows/*.parquet')").fetchall()
    assert n == [(2,)]
    assert stem_of({"case": "case-a", "side": "old", "repeat": 2}) == names[0][:-8]
