"""The storage bench's runner, rows, report and slicer (ADR 0109 step 3)."""

import json
from argparse import Namespace

import numpy as np
import pytest

pytest.importorskip("zarr")
duckdb = pytest.importorskip("duckdb")

from benchmarks.ingest.generate import Case, ensure
from benchmarks.storage import report, rows, run
from benchmarks.storage.__main__ import main
from benchmarks.storage.slice import slice_frames
from tests.test_bench_storage_variants import make_source


def _row(case, variant, repeat, jitter, *, real=False, cache="warm", scan=1.0, disk=1000):
    """A completed row; every time measure is a base value times (1 + jitter)."""
    base = {"write_s": 2.0, "write_cpu_s": 1.0, "scan_s": scan, "scan_cpu_s": 0.9,
            "frame_p50_ms": 1.0, "frame_p95_ms": 2.0, "frames_total_s": 3.0, "reopen_s": 0.01}
    row = {c: None for c in rows.COLUMNS}
    row.update({k: v * (1 + jitter) for k, v in base.items()})
    row.update(run_set="t", case=case, real=real, variant=variant, repeat=repeat, order_index=repeat,
               cache=cache, completed=True, disk_bytes=disk, disk_alloc_bytes=disk, n_files=3,
               peak_rss_bytes=100 * 2**20, source_bytes=5000, frames=10, atoms=100,
               blosc_nthreads=1, host="h", cpu_model="c", ram_bytes=2**34, os="o", python="3")
    return row


def _write(tmp_path, spec):
    """``spec``: variant -> dict of _row options; five repeats with small jitter."""
    out = tmp_path / "out"
    out.mkdir()
    (out / "run_set.json").write_text(json.dumps({"host_facts": {"host": "h", "cpu_model": "c",
                                                                 "ram_bytes": 2**34, "os": "o"},
                                                  "versions": {"zarr": "3.1.6", "numcodecs": "0.16"}}))
    for case, real in (("synthetic", False), ("real", True)):
        for variant, options in spec.items():
            for repeat in range(1, 6):
                rows.write_row(_row(case, variant, repeat, 0.01 * repeat, real=real, **options),
                               out / "rows")
    return out


def test_report_rules_out_a_slower_variant_and_keeps_a_smaller_one(tmp_path):
    out = _write(tmp_path, {
        "npz": {},
        "npz-copy": {},                               # same code: must read no clear change
        "zarr-slow": {"scan": 1.5, "disk": 600},      # smaller but 50 % slower scan
        "zarr-small": {"disk": 600},                  # 40 % smaller, nothing worse
        "zarr-nogain": {"disk": 1000},                # equal in everything: dominated on real data
    })
    text, analysis = report.build_report(out)
    assert set(analysis["ruled_out"]) == {"zarr-slow", "zarr-nogain"}
    assert "scan_s worse" in analysis["ruled_out"]["zarr-slow"][0]
    assert "dominated" in analysis["ruled_out"]["zarr-nogain"][0]
    assert analysis["survivors"] == ["zarr-small"]
    assert analysis["go"]["zarr-small"]["go"] and "40% fewer bytes" in analysis["go"]["zarr-small"]["why"]
    assert "A/A check" in text and "It did." in text
    assert "| zarr-small |" in text and "bytes on disk" in text


def test_a_cold_scan_gain_counts_when_the_disk_saving_is_small(tmp_path):
    out = tmp_path / "out"
    spec = {"npz": {"scan": 2.0}, "zarr-fast": {"scan": 1.0, "disk": 900}}
    for case in ("real",):
        for variant, options in spec.items():
            for repeat in range(1, 6):
                rows.write_row(_row(case, variant, repeat, 0.01 * repeat, real=True, cache="cold",
                                    **options), out / "rows")
    _, analysis = report.build_report(out)
    assert analysis["go"]["zarr-fast"]["go"]
    assert "cold scan" in analysis["go"]["zarr-fast"]["why"]


def test_order_rotates_and_alternates():
    names = ["a", "b", "c", "d"]
    assert run.order_for(names, 1) == ["b", "c", "d", "a"]
    assert run.order_for(names, 2) == ["c", "b", "a", "d"]
    assert run.order_for(["a", "b"], 1) == ["b", "a"] and run.order_for(["a", "b"], 2) == ["a", "b"]
    firsts = {run.order_for(names, r)[0] for r in range(1, 9)}
    assert firsts == set(names)


def test_slice_cuts_at_a_frame_boundary(tmp_path):
    path, manifest = ensure(Case("100MB", "small", "variable"), tmp_path / "data", workers=1,
                            target_bytes=300_000)
    out = tmp_path / "slice.extxyz"
    frames, size = slice_frames(path, out, 100_000)
    data = path.read_bytes()
    assert out.read_bytes() == data[:size]
    assert size >= 100_000 and 0 < frames < manifest["frames"]
    lines = out.read_text().split("\n")
    assert lines[-1] == ""
    i = n = before_last = 0
    while i < len(lines) - 1:
        before_last = len("\n".join(lines[:i]).encode()) + (1 if i else 0)
        i += int(lines[i]) + 2
        n += 1
    assert i == len(lines) - 1 and n == frames
    # Without the last frame the slice would have been short of the target.
    assert before_last < 100_000 <= size
    with pytest.raises(FileExistsError):
        slice_frames(path, out, 100_000)


def _args(tmp_path, source, **over):
    base = dict(source=[source], variants="npz,zarr-zstd1-16k", repeats=2, cold=False,
                output=tmp_path / "out", store_dir=tmp_path / "stores", keep_stores=False, timeout=600)
    return Namespace(**{**base, **over})


def test_two_variants_two_repeats_write_the_expected_rows(tmp_path):
    source = make_source(tmp_path / "src", variable=True, windows=2, frames=40).folder
    assert run.run_matrix(_args(tmp_path, source)) == 0
    got = duckdb.sql(f"SELECT variant, repeat, cache, completed, write_s, scan_s, frame_p95_ms, reopen_s, "
                     f"disk_bytes, n_files, peak_rss_bytes, blosc_nthreads FROM "
                     f"read_parquet('{tmp_path}/out/rows/*.parquet') ORDER BY order_index").fetchall()
    assert [(g[0], g[1]) for g in got] == [("zarr-zstd1-16k", 1), ("npz", 1),
                                           ("npz", 2), ("zarr-zstd1-16k", 2)]
    assert all(g[2] == "warm" and g[3] for g in got)
    assert all(all(x and x > 0 for x in g[4:11]) for g in got)
    assert {g[11] for g in got if g[0].startswith("zarr")} == {1}
    assert not list((tmp_path / "stores").glob("*"))          # stores deleted after their runs
    assert (tmp_path / "out" / "raw" / "src" / "npz" / "r1" / "check.json").is_file()
    assert report.build_report(tmp_path / "out")[0].startswith("# Storage bench")


def test_a_variant_that_is_not_bit_identical_is_refused_before_timing(tmp_path, monkeypatch):
    source = make_source(tmp_path / "src", variable=False, windows=2, frames=40).folder
    real_call = run.call

    def broken(op, variant, source_, store, timeout):
        if op == "check" and variant == "zarr-none-16k":
            return {"ok": False, "bad": ["window 0 column positions"]}
        return real_call(op, variant, source_, store, timeout)

    monkeypatch.setattr(run, "call", broken)
    assert run.run_matrix(_args(tmp_path, source, variants="npz,zarr-none-16k")) == 0
    got = duckdb.sql(f"SELECT variant, repeat, completed, error, scan_s FROM "
                     f"read_parquet('{tmp_path}/out/rows/*.parquet') WHERE variant = 'zarr-none-16k'").fetchall()
    assert len(got) == 1 and not got[0][2] and "not bit-identical" in got[0][3] and got[0][4] is None


def test_cold_is_refused_without_a_sudo_password(tmp_path, monkeypatch, capsys):
    source = make_source(tmp_path / "src", variable=False, windows=1, frames=20).folder
    monkeypatch.setattr(run, "can_purge", lambda: False)
    assert run.run_matrix(_args(tmp_path, source, cold=True)) == 2
    assert "sudo -n purge" in capsys.readouterr().err and not (tmp_path / "out").exists()


def test_an_existing_output_and_an_unknown_variant_are_refused(tmp_path, capsys):
    source = make_source(tmp_path / "src", variable=False, windows=1, frames=20).folder
    (tmp_path / "out").mkdir()
    assert run.run_matrix(_args(tmp_path, source)) == 2
    assert "already exists" in capsys.readouterr().err
    assert run.run_matrix(_args(tmp_path, source, output=tmp_path / "o2", variants="zarr-lz9-16k")) == 2
    assert main(["report", str(tmp_path / "missing")]) == 2
