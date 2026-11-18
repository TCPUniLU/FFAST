"""End-to-end ingestion benchmark run on a tiny file (ADR 0105 §5)."""

from argparse import Namespace

import pytest

duckdb = pytest.importorskip("duckdb")

from benchmarks.ingest import run
from benchmarks.ingest.run import run_matrix


def _args(tmp_path, **over):
    base = dict(side=[f"new={run.HARNESS_ROOT}"], sizes=["100MB"], classes=["small"],
                counts=["constant"], repeats=1, data_dir=tmp_path / "data",
                output=tmp_path / "out", timeout=600, workers=1, delete_data_after=True,
                target_bytes=1_500_000)
    return Namespace(**{**base, **over})


@pytest.mark.integration
def test_one_side_one_repeat_gives_two_completed_rows(tmp_path):
    assert run_matrix(_args(tmp_path)) == 0
    got = duckdb.sql(
        f"SELECT scenario, completed, load_s, basic_errors_s, cache_bytes FROM "
        f"read_parquet('{tmp_path}/out/rows/*.parquet') ORDER BY scenario").fetchall()
    assert [g[0] for g in got] == ["first_open", "reopen"]
    assert all(g[1] and g[2] > 0 and g[3] > 0 and g[4] > 0 for g in got)
    assert not list((tmp_path / "data").glob("*.extxyz"))  # deleted after the case
    assert list((tmp_path / "data").glob("*.json"))        # manifest kept


def test_an_existing_output_is_refused(tmp_path, capsys):
    (tmp_path / "out").mkdir()
    assert run_matrix(_args(tmp_path)) == 2
    assert "already exists" in capsys.readouterr().err


def test_an_unknown_class_is_refused(tmp_path, capsys):
    assert run_matrix(_args(tmp_path, classes=["huge"])) == 2
    assert "huge" in capsys.readouterr().err
