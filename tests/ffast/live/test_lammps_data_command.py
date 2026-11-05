"""`ffast lammps data` and the identity record (ADR 0086, decision D2 of the plan)."""

from __future__ import annotations

import numpy as np
import pytest

from ffast.live.cli import main
from ffast.live.data_file import coordinate_hash, find_record
from ffast.live.dump_reader import DumpReader
from tests.ffast.live.bundle_helpers import INITIAL, initial_atoms, make_bundle
from tests.ffast.live.dump_helpers import dump_text


@pytest.fixture
def records(tmp_path, monkeypatch):
    d = tmp_path / "records"
    monkeypatch.setenv("FFAST_LAMMPS_DATA_RECORDS", str(d))
    return d


@pytest.fixture(scope="module")
def bundle(tmp_path_factory):
    return make_bundle(tmp_path_factory.mktemp("b"))[0]


def _parse_data(text):
    lines = text.splitlines()
    n = int(lines[2].split()[0])
    ntypes = int(lines[3].split()[0])
    masses_at = lines.index("Masses") + 2
    masses = {int(l.split()[0]): float(l.split()[1]) for l in lines[masses_at:masses_at + ntypes]}
    atoms_at = lines.index("Atoms # atomic") + 2
    rows = [l.split() for l in lines[atoms_at:atoms_at + n]]
    return n, ntypes, masses, rows


def _run(bundle, tmp_path, *extra):
    out = tmp_path / "run.data"
    rc = main(["lammps", "data", "--from", str(INITIAL), "--bundle", str(bundle),
               "--elements", "C H O", "-o", str(out), *extra])
    return rc, out


def test_the_data_file_lists_atoms_in_reference_order_with_exact_coordinates(bundle, tmp_path, records):
    rc, out = _run(bundle, tmp_path)
    assert rc == 0
    n, ntypes, masses, rows = _parse_data(out.read_text())
    atoms = initial_atoms()
    assert n == 21 and ntypes == 3
    assert masses[1] == pytest.approx(12.011, abs=1e-3) and masses[3] == pytest.approx(15.999, abs=1e-3)
    assert [int(r[0]) for r in rows] == list(range(1, 22))
    types = {"C": 1, "H": 2, "O": 3}
    assert [int(r[1]) for r in rows] == [types[s] for s in atoms.get_chemical_symbols()]
    pos = np.array([[float(v) for v in r[2:]] for r in rows])
    assert np.array_equal(pos, atoms.get_positions())


def test_the_record_matches_a_dump_frame_with_the_same_coordinates(bundle, tmp_path, records):
    _run(bundle, tmp_path)
    _, _, _, rows = _parse_data((tmp_path / "run.data").read_text())
    pos = np.array([[float(v) for v in r[2:]] for r in rows])
    dump = tmp_path / "frames.lammpstrj"
    dump.write_text(dump_text([pos]))
    frame = DumpReader(dump).poll()[0]
    rec = find_record(frame.positions)
    assert rec is not None and rec["data_file"] == str(tmp_path / "run.data")
    assert len(list(records.glob("*.json"))) == 1
    moved = frame.positions.copy()
    moved[0, 0] = np.nextafter(moved[0, 0], np.inf)        # one ulp
    assert find_record(moved) is None
    assert coordinate_hash(moved) != coordinate_hash(frame.positions)


def test_a_frame_in_another_atom_order_is_refused(bundle, tmp_path, records, capsys):
    from ase.io import write

    a = initial_atoms()
    swapped = a[[1, 0] + list(range(2, 21))]
    swapped = swapped[[13] + [i for i in range(21) if i != 13]]     # an H first
    write(str(tmp_path / "s.xyz"), swapped)
    rc = main(["lammps", "data", "--from", str(tmp_path / "s.xyz"), "--bundle", str(bundle),
               "--elements", "C H O", "-o", str(tmp_path / "run.data")])
    assert rc == 1
    assert "bundle's order" in capsys.readouterr().err
    assert not (tmp_path / "run.data").exists()


def test_an_index_outside_the_file_is_refused(bundle, tmp_path, records, capsys):
    rc, _ = _run(bundle, tmp_path, "--index", "5")
    assert rc == 1 and "outside" in capsys.readouterr().err
