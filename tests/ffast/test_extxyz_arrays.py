"""The fast extxyz reader gives ASE's numbers, byte for byte (read-once plan
2026-11-02, ADR 0076), and refuses what it does not handle."""
import numpy as np
import pytest
import ase.io
from ase.calculators.calculator import all_properties
from ase.io.extxyz import REV_PROPERTY_NAME_MAP, per_config_properties

from ffast.io import extxyz_arrays as xa
from ffast.io import frame_index as fi
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
    assert fa.missing["spin"].tolist() == [True] * 4 + [False] * 2
    assert fa.columns["forces"].shape == (6, 3)
    assert "forces" not in fa.missing                        # every frame has it


def test_a_nan_written_in_the_file_is_not_missing(tmp_path):
    """Missing means "this frame has no such column"; a NaN in the data is a
    bad value, as before (ADR 0064 section 8)."""
    p = tmp_path / "nan.extxyz"
    p.write_text("1\nProperties=species:S:1:pos:R:3 energy=1.0\nH 0 0 0\n"
                 "1\nProperties=species:S:1:pos:R:3:q:R:1 energy=2.0\nH 0 0 1 nan\n")
    fa = xa.read_frames(fi.scan(str(p)), workers=1)
    assert np.isnan(fa.columns["q"]).all()
    assert fa.missing["q"].tolist() == [True, False]


def test_no_column_missing_gives_no_masks(tmp_path):
    fa = xa.read_frames(fi.scan(_write(tmp_path, "variable.extxyz")), workers=1)
    assert fa.missing == {}


def test_extra_columns_fill_across_batches(tmp_path, monkeypatch):
    monkeypatch.setattr(xa, "MIN_BATCH", 1)
    fa = xa.read_frames(fi.scan(_write(tmp_path, "columns_change.extxyz")), workers=1, batch=1)
    m = fa.columns["spin"]
    assert np.isnan(m[:4]).all() and m[4:].tolist() == [0.5, -0.5]
    assert fa.missing["spin"].tolist() == [True] * 4 + [False] * 2


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


def test_plain_xyz_reads_like_ase(tmp_path):
    path = _write(tmp_path, "plain.xyz")
    fa = xa.read_frames(fi.scan(path), workers=1)
    _assert_like_ase(path, fa)
    assert fa.values == {} and fa.columns == {}


def test_a_plain_header_like_a_pimd_one_reads_like_ase(tmp_path):
    p = tmp_path / "pimd.xyz"
    head = "# CELL(abcABC):  105.83544   105.83544   105.83544    90.00000  Step:  {}  Bead:  9 positions{{angstrom}}"
    p.write_text("".join(f"2\n{head.format(i)}\n  C  2.13102e+00 -3.39153e-01  1.45088e-02\n"
                         f"  H  1.0e-01 2.0 3.0\n" for i in range(3)))
    _assert_like_ase(str(p), xa.read_frames(fi.scan(str(p)), workers=1))


def test_plain_xyz_with_extra_columns_is_not_fast(tmp_path):
    p = tmp_path / "extra.xyz"
    p.write_text("1\ncomment\nH 0 0 0 1.5\n")      # ASE drops the 5th column; ASE reads it
    with pytest.raises(xa.NotFast):
        xa.read_frames(fi.scan(str(p)), workers=1)


def test_a_bare_pbc_word_in_a_plain_header_is_left_to_ase(tmp_path):
    p = tmp_path / "pbc.xyz"
    p.write_text("1\nperiodic pbc run\nH 0 0 0\n")
    with pytest.raises(xa.NotFast):
        xa.read_frames(fi.scan(str(p)), workers=1)


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


# ── memory (ADR 0083) ───────────────────────────────────────────────────────

def _free(monkeypatch, *values):
    """available_memory() answers ``values`` in turn, then the last one."""
    from ffast.metrics import resources
    left = list(values)
    monkeypatch.setattr(resources, "available_memory", lambda: left.pop(0) if len(left) > 1 else left[0])


def test_a_file_that_does_not_fit_is_refused_before_reading(tmp_path, monkeypatch):
    path = _write(tmp_path, "variable.extxyz")
    _free(monkeypatch, 1000)
    parsed = []
    real = xa._parse_range
    monkeypatch.setattr(xa, "_parse_range", lambda *a: parsed.append(a) or real(*a))
    with pytest.raises(xa.NotEnoughMemory) as err:
        xa.read_frames(fi.scan(path), workers=1)
    assert "variable.extxyz needs about" in str(err.value) and "is free" in str(err.value)
    assert len(parsed) == 1                      # frame 0 alone, to size the arrays


def test_a_memory_error_is_not_a_reason_to_read_with_ase():
    assert not issubclass(xa.NotEnoughMemory, xa.NotFast)


def test_fewer_workers_when_all_of_them_do_not_fit():
    gb = 2**30
    assert xa._plan(4 * gb, 1 * gb, 8, 10 * gb) == (5, True, 9 * gb)
    assert xa._plan(4 * gb, 1 * gb, 8, 100 * gb) == (8, True, 12 * gb)
    assert xa._plan(9 * gb, 1 * gb, 8, 10 * gb) == (1, False, 10 * gb)


def test_reading_stops_when_free_memory_runs_low(tmp_path, monkeypatch):
    monkeypatch.setattr(xa, "MIN_BATCH", 1)
    path = _write(tmp_path, "variable.extxyz")
    _free(monkeypatch, 100 * 2**30, 100 * 2**30, 1)   # the check, batch 1, then nearly nothing
    with pytest.raises(xa.NotEnoughMemory) as err:
        xa.read_frames(fi.scan(path), workers=1, batch=1)
    assert "after 2 of 3 frames" in str(err.value)


def test_the_last_batch_never_stops_a_read(tmp_path, monkeypatch):
    path = _write(tmp_path, "variable.extxyz")
    _free(monkeypatch, 100 * 2**30, 1)
    assert len(xa.read_frames(fi.scan(path), workers=1)) == 3


def test_frame_counts_that_disagree_with_the_index_are_not_fast(tmp_path):
    index = fi.scan(_write(tmp_path, "variable.extxyz"))
    index.counts = index.counts - 1
    with pytest.raises(xa.NotFast):
        xa.read_frames(index, workers=1)


# ── every n-th frame (ADR 0083) ─────────────────────────────────────────────

def _many(tmp_path, n=23):
    from ase import Atoms
    from ase.calculators.singlepoint import SinglePointCalculator
    rng = np.random.default_rng(1)
    frames = []
    for i in range(n):
        a = Atoms("H" * (1 + i % 4), positions=rng.random((1 + i % 4, 3)))
        a.calc = SinglePointCalculator(a, energy=float(i), forces=rng.random((1 + i % 4, 3)))
        frames.append(a)
    p = tmp_path / "many.extxyz"
    ase.io.write(p, frames, format="extxyz")
    return str(p)


@pytest.mark.parametrize("through", [16, 0])        # read straight through / a seek per frame
@pytest.mark.parametrize("step", [2, 7])
def test_every_nth_frame_reads_like_ase(tmp_path, monkeypatch, step, through):
    monkeypatch.setattr(xa, "READ_THROUGH", through)
    monkeypatch.setattr(xa, "MIN_BATCH", 1)
    path = _many(tmp_path)
    fa = xa.read_frames(fi.scan(path).every(step), workers=1, batch=2)
    frames = ase.io.read(path, index=slice(0, None, step))
    assert _same(fa.counts, [len(a) for a in frames])
    assert _same(fa.positions, np.vstack([a.get_positions() for a in frames]))
    assert _same(fa.columns["forces"], np.vstack([a.get_forces() for a in frames]))
    assert _same(fa.values["energy"], np.array([a.get_potential_energy() for a in frames]))


def test_a_strided_index_counts_and_reads_its_own_frames(tmp_path):
    path = _many(tmp_path)
    index = fi.scan(path).every(7)
    assert len(index) == 4                                     # frames 0, 7, 14, 21
    assert index.atom_counts().tolist() == [1, 4, 3, 2]
    assert index.read_one(-1).get_potential_energy() == 21.0
    assert [a.get_potential_energy() for a in index.read(1, 3)] == [7.0, 14.0]


def test_a_strided_load_goes_through_the_fast_reader(tmp_path, monkeypatch):
    from ffast.core.data_types import AtomsList
    from ffast.loaders.ase import aseDatasetLoader
    path = _many(tmp_path)
    used = []
    real = xa.read_frames
    monkeypatch.setattr(xa, "read_frames", lambda index, **kw: used.append(index) or real(index, **kw))
    loader = aseDatasetLoader(path, atomsList=AtomsList(path, step=7))
    assert len(used) == 1 and loader.N == 4
    assert loader.E.tolist() == [0.0, 7.0, 14.0, 21.0]

