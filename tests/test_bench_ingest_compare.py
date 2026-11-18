"""Verdicts of the ingestion benchmark comparison (ADR 0105 §5)."""

import pytest

pytest.importorskip("duckdb")

from benchmarks.ingest.compare import CompareError, build_report, compare
from benchmarks.ingest.rows import COLUMNS, write_rows

BASE = [10.0, 10.1, 9.9, 10.05, 9.95]


def _rows(side, loads, *, sha="s1", host="box", set_name="a", completed=None, cache=1000):
    out = []
    for i, load in enumerate(loads):
        for scenario in ("first_open", "reopen"):
            row = dict.fromkeys(COLUMNS)
            ok = True if completed is None else completed[i]
            row.update(run_set=set_name, side=side, commit="c" + side, dirty=False,
                       case="100MB-small-constant-s0-g1", size="100MB", atoms="small",
                       counts="constant", file_bytes=100, dataset_sha256=sha, repeat=i + 1,
                       scenario=scenario, load_s=load if ok else None,
                       basic_errors_s=5.0 if ok else None, peak_rss_bytes=2**30 if ok else None,
                       cache_bytes=cache, completed=ok, host=host, cpu_model="cpu")
            out.append(row)
    return out


def _set(tmp_path, base, cand, **kw):
    folder = tmp_path / kw.pop("folder", "out")
    write_rows(_rows("old", base, **kw), folder / "rows", "old")
    write_rows(_rows("new", cand, **kw), folder / "rows", "new")
    return folder


def _verdict(results, measure="load_s", scenario="first_open"):
    return next(r["verdict"] for r in results
                if r["measure"] == measure and r["scenario"] == scenario)


def test_a_clear_slowdown_is_worse(tmp_path):
    folder = _set(tmp_path, BASE, [x * 1.2 for x in BASE])
    text, results = build_report(folder, "old", "new")
    assert _verdict(results) == "worse"
    assert "worse" in text and "Reopen" in text


def test_a_slowdown_with_overlapping_spread_is_no_clear_change(tmp_path):
    folder = _set(tmp_path, BASE, [9.0, 14.0, 12.0, 10.0, 15.0])
    _, results = build_report(folder, "old", "new")
    assert _verdict(results) == "no clear change"


def test_a_small_change_is_no_clear_change(tmp_path):
    folder = _set(tmp_path, BASE, [x * 1.05 for x in BASE])
    _, results = build_report(folder, "old", "new")
    assert _verdict(results) == "no clear change"


def test_a_clear_speedup_is_better(tmp_path):
    folder = _set(tmp_path, BASE, [x * 0.8 for x in BASE])
    _, results = build_report(folder, "old", "new")
    assert _verdict(results) == "better"


def test_different_data_is_refused(tmp_path):
    folder = tmp_path / "out"
    write_rows(_rows("old", BASE, sha="one"), folder / "rows", "old")
    write_rows(_rows("new", BASE, sha="two"), folder / "rows", "new")
    with pytest.raises(CompareError, match="different data"):
        build_report(folder, "old", "new")


def test_different_hosts_across_run_sets_are_refused(tmp_path):
    a = tmp_path / "a"
    b = tmp_path / "b"
    write_rows(_rows("old", BASE, host="laptop"), a / "rows", "old")
    write_rows(_rows("new", BASE, host="cluster"), b / "rows", "new")
    with pytest.raises(CompareError, match="different machines"):
        build_report(a, "old", "new", [b])


def test_a_second_run_set_adds_its_side(tmp_path):
    a = tmp_path / "a"
    b = tmp_path / "b"
    write_rows(_rows("old", BASE), a / "rows", "old")
    write_rows(_rows("new", [x * 1.2 for x in BASE]), b / "rows", "new")
    _, results = build_report(a, "old", "new", [b])
    assert _verdict(results) == "worse"


def test_failed_rows_are_left_out_and_counted(tmp_path):
    folder = tmp_path / "out"
    write_rows(_rows("old", BASE), folder / "rows", "old")
    cand = [x * 1.2 for x in BASE] + [99.0]
    write_rows(_rows("new", cand, completed=[True] * 5 + [False]), folder / "rows", "new")
    text, results = build_report(folder, "old", "new")
    row = next(r for r in results if r["measure"] == "load_s" and r["scenario"] == "first_open")
    assert row["n_candidate"] == 5
    assert "2 failed scenario rows" in text


def test_compare_writes_the_markdown_file(tmp_path, capsys):
    folder = _set(tmp_path, BASE, BASE)
    assert compare(folder, "old", "new") == 0
    assert (folder / "compare-old-vs-new.md").read_text().startswith("# Ingestion benchmark")
    assert compare(folder, "old", "missing") == 2
