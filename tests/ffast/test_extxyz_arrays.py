"""The fast extxyz reader gives ASE's numbers, byte for byte (read-once plan
2026-11-02, ADR 0076), and refuses what it does not handle."""
import numpy as np
import pytest
import ase.io
from ase.calculators.calculator import all_properties
from ase.io.extxyz import REV_PROPERTY_NAME_MAP, per_config_properties

from ffast.io import extxyz_arrays as xa
from ffast.io import frame_index as fi
from ffast.io.missing import MISSING, is_missing
from tests.ffast.read_once_fixtures import FILES, REPO_FILES


def _same(a, b):
    a, b = np.asarray(a), np.asarray(b)
    return a.dtype == b.dtype and a.shape == b.shape and a.tobytes() == b.tobytes()


def _write(tmp_path, name):
    p = tmp_path / name
    p.write_text(FILES[name])
    return str(p)


def _assert_like_ase(path, fa):
    frames = ase.io.read(path, index=":")
    assert _same(fa.counts, [len(a) for a in frames])
    assert _same(fa.numbers, np.concatenate([a.get_atomic_numbers() for a in frames]))
    assert _same(fa.positions, np.vstack([a.get_positions() for a in frames]))
    assert _same(fa.cells, np.array([a.get_cell().array for a in frames]))
    assert _same(fa.pbc, np.array([a.pbc for a in frames]))
    for name, col in fa.columns.items():
        ase_name = REV_PROPERTY_NAME_MAP.get(name, name)
        where = (lambda a: a.calc.results[ase_name]) if ase_name in all_properties else (lambda a: a.arrays[ase_name])
        assert _same(col, np.concatenate([where(a) for a in frames])), name
    for key, v in fa.values.items():
        where = (lambda a: a.calc.results[key]) if key in per_config_properties else (lambda a: a.info[key])
        assert _same(v, np.array([where(a) for a in frames])), key


@pytest.mark.parametrize("name", ["uniform.extxyz", "variable.extxyz"])
def test_fixture_files_read_like_ase(tmp_path, name):
    path = _write(tmp_path, name)
    _assert_like_ase(path, xa.read_frames(fi.scan(path), workers=1))


@pytest.mark.parametrize("path", REPO_FILES)
def test_repo_example_files_read_like_ase(path):
    _assert_like_ase(path, xa.read_frames(fi.scan(path), workers=1))


def test_header_values_follow_ase(tmp_path):
    fa = xa.read_frames(fi.scan(_write(tmp_path, "variable.extxyz")), workers=1)
    # whole numbers stay int64, as ASE keeps them; the quoted energy=999 is text
    assert fa.values["energy"].dtype == np.int64
    assert list(fa.values["energy"]) == [-5, -7, -1]
    assert list(fa.values["gap"]) == [1.25, 2.5, 0.75]
    assert "tag" not in fa.values and "note" not in fa.values
    assert list(fa.numbers) == [6, 8, 7, 1, 1, 18]          # c, o -> C, O
    assert not fa.pbc.any() and not fa.cells.any()           # no Lattice


def test_lattice_rows_and_pbc(tmp_path):
    fa = xa.read_frames(fi.scan(_write(tmp_path, "uniform.extxyz")), workers=1)
    assert fa.cells[0].tolist() == [[5.0, 0.1, 0.0], [0.2, 6.0, 0.0], [0.3, 0.4, 7.0]]
    assert fa.pbc[0].tolist() == [True, False, True]
    assert fa.values["energy"].dtype == np.float64           # -11 among floats


def test_an_extra_number_column_is_nan_where_a_frame_has_none(tmp_path):
    fa = xa.read_frames(fi.scan(_write(tmp_path, "columns_change.extxyz")), workers=1)
    m = fa.columns["spin"]
    assert m.dtype == np.float64 and m.shape == (6,)
    assert np.isnan(m[:4]).all() and m[4:].tolist() == [0.5, -0.5]
    assert is_missing(m).tolist() == [True] * 4 + [False] * 2
    assert fa.columns["forces"].shape == (6, 3)


def test_a_nan_written_in_the_file_is_not_missing(tmp_path):
    """Missing means "this frame has no such column"; a NaN in the data is a
    bad value, as before (ADR 0064 section 8)."""
    p = tmp_path / "nan.extxyz"
    p.write_text("1\nProperties=species:S:1:pos:R:3 energy=1.0\nH 0 0 0\n"
                 "1\nProperties=species:S:1:pos:R:3:q:R:1 energy=2.0\nH 0 0 1 nan\n")
    q = xa.read_frames(fi.scan(str(p)), workers=1).columns["q"]
    assert np.isnan(q).all()
    assert is_missing(q).tolist() == [True, False]


def test_missing_survives_copies_slices_and_pickling():
    import pickle
    a = np.array([1.0, MISSING, np.nan, 2.0])
    want = [False, True, False, False]
    assert is_missing(a.copy()).tolist() == want
    assert is_missing(a.astype(np.float64)).tolist() == want
    assert is_missing(pickle.loads(pickle.dumps(a))).tolist() == want
    assert is_missing(np.concatenate([a, a])).tolist() == want * 2
    assert is_missing(a[[1, 2]]).tolist() == [True, False]


def test_extra_columns_fill_across_batches(tmp_path, monkeypatch):
    monkeypatch.setattr(xa, "MIN_BATCH", 1)
    fa = xa.read_frames(fi.scan(_write(tmp_path, "columns_change.extxyz")), workers=1, batch=1)
    m = fa.columns["spin"]
    assert np.isnan(m[:4]).all() and m[4:].tolist() == [0.5, -0.5]
    assert is_missing(m[:4]).all()


def test_an_integer_extra_column_becomes_float_with_nan(tmp_path):
    p = tmp_path / "ints.extxyz"
    p.write_text("1\nProperties=species:S:1:pos:R:3 energy=1.0\nH 0 0 0\n"
                 "1\nProperties=species:S:1:pos:R:3:tag:I:1 energy=2.0\nH 0 0 1 7\n")
    fa = xa.read_frames(fi.scan(str(p)), workers=1)
    assert fa.columns["tag"].dtype == np.float64
    assert np.isnan(fa.columns["tag"][0]) and fa.columns["tag"][1] == 7


def test_an_extra_column_that_changes_width_is_refused(tmp_path):
    p = tmp_path / "wide.extxyz"
    p.write_text("1\nProperties=species:S:1:pos:R:3:q:R:1 energy=1.0\nH 0 0 0 1\n"
                 "1\nProperties=species:S:1:pos:R:3:q:R:2 energy=2.0\nH 0 0 1 1 2\n")
    with pytest.raises(xa.PropertiesDiffer):
        xa.read_frames(fi.scan(str(p)), workers=1)


def test_changed_essential_columns_are_refused_with_the_frame(tmp_path):
    with pytest.raises(xa.PropertiesDiffer) as err:
        xa.read_frames(fi.scan(_write(tmp_path, "forces_change.extxyz")), workers=1)
    assert err.value.frame == 2
    assert "forces" in str(err.value)


def test_changed_essential_columns_across_batches(tmp_path, monkeypatch):
    monkeypatch.setattr(xa, "MIN_BATCH", 1)
    with pytest.raises(xa.PropertiesDiffer) as err:
        xa.read_frames(fi.scan(_write(tmp_path, "forces_change.extxyz")), workers=1, batch=1)
    assert err.value.frame == 2


def test_plain_xyz_is_not_fast(tmp_path):
    with pytest.raises(xa.NotFast):
        xa.read_frames(fi.scan(_write(tmp_path, "plain.xyz")), workers=1)


def test_logical_column_is_not_fast(tmp_path):
    p = tmp_path / "mask.extxyz"
    p.write_text("1\nProperties=species:S:1:pos:R:3:move_mask:L:1 energy=1.0\nH 0 0 0 T\n")
    with pytest.raises(xa.NotFast):
        xa.read_frames(fi.scan(str(p)), workers=1)


def test_long_numbers_are_not_cut(tmp_path):
    p = tmp_path / "long.extxyz"
    x = "0.123456789012345678901234567890123"
    p.write_text(f"1\nProperties=species:S:1:pos:R:3 energy=1.0\nH {x} 0 0\n")
    fa = xa.read_frames(fi.scan(str(p)), workers=1)
    assert fa.positions[0, 0] == float(x)


def test_unreadable_header_is_not_fast(tmp_path):
    p = tmp_path / "junk.extxyz"
    p.write_text('1\nProperties=species:S:1:pos:R:3 note="unclosed energy=1.0\nH 0 0 0\n')
    with pytest.raises(xa.NotFast):
        xa.read_frames(fi.scan(str(p)), workers=1)


def test_workers_give_the_same_bytes(tmp_path, monkeypatch):
    monkeypatch.setattr(xa, "PARALLEL_MIN_FRAMES", 0)
    monkeypatch.setattr(xa, "MIN_BATCH", 1)
    index = fi.scan(_write(tmp_path, "variable.extxyz"))
    one = xa.read_frames(index, workers=1)
    seen = []
    two = xa.read_frames(index, workers=2, batch=1, progress=lambda done, n: seen.append((done, n)))
    for k in ("counts", "numbers", "positions", "cells", "pbc"):
        assert _same(getattr(one, k), getattr(two, k)), k
    assert one.columns.keys() == two.columns.keys()
    assert all(_same(one.columns[k], two.columns[k]) for k in one.columns)
    assert one.values.keys() == two.values.keys()
    assert all(_same(one.values[k], two.values[k]) for k in one.values)
    assert seen == [(1, 3), (2, 3), (3, 3)]


def test_worker_count_from_environment(monkeypatch):
    monkeypatch.setenv("FFAST_READ_WORKERS", "3")
    assert xa.worker_count() == 3
    monkeypatch.setenv("FFAST_READ_WORKERS", "0")
    assert xa.worker_count() == 1
