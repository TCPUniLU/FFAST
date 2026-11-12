"""Readers can write (ADR 0095, decision 11): ``open_writer(path, contents,
format)`` gives an object with ``write(chunk)`` and ``close()``, in the same
dict shape ``read()`` returns; ``write_formats`` says what each format holds."""
import json

import ase.io
import numpy as np
import pytest
from ase import Atoms
from ase.calculators.singlepoint import SinglePointCalculator

from ffast.readers import ReaderError, check_reader, chunks, registry
from ffast.readers.ase_reader import AseReader
from ffast.readers.dataset import ReaderDataset
from ffast.readers.npz_reader import NpzReader
from ffast.readers.writing import ExportItem, check_writable, write_dataset
from tests.ffast.readers.test_ase_reader import _frames, _write
from tests.ffast.readers.toy_reader import ToyTextReader, make_frames, write_toy


def _dataset(path, reader):
    ds = ReaderDataset(path, chunks.read_arrays(reader, path), reader.name)
    ds.initialise()
    return ds


def _reference_item(ds):
    return ExportItem(
        energy_name="energy", force_name="forces",
        energy=(lambda i: ds.getEnergies(i)) if ds.E is not None else None,
        forces=(lambda i: ds.getForces(i)) if ds.hasForces() else None)


# ── the declarations ────────────────────────────────────────────────────────

def test_the_bundled_writers_are_listed_with_what_each_format_can_hold():
    formats = {f["format"]: f for f in registry.writable_formats()}
    assert list(formats) == ["extxyz", "xyz", "db", "traj", "vasp", "dftb", "sgdml"]
    assert formats["extxyz"]["label"] == "Extended XYZ" and formats["extxyz"]["ext"] == ".extxyz"
    assert formats["sgdml"]["can_write"]["energies"] == 1
    assert formats["sgdml"]["can_write"]["variable_atoms"] is False
    assert formats["vasp"]["can_write"]["frames"] == 1 and formats["vasp"]["can_write"]["energies"] == 0
    assert registry.writer_for("sgdml") is NpzReader
    assert registry.writer_for("extxyz") is AseReader
    assert registry.writer_for("nothing") is None


# ── round trips: write what was read, read it back ──────────────────────────

def test_the_ase_reader_round_trips_through_its_writer(tmp_path):
    check_reader(AseReader, _write(tmp_path, _frames(5, variable=True), "v.traj"))
    check_reader(AseReader, _write(tmp_path, _frames(5), "a.extxyz"))


def test_the_npz_reader_round_trips_through_its_writer(tmp_path):
    path = str(tmp_path / "d.npz")
    rng = np.random.default_rng(1)
    np.savez(path, R=rng.normal(size=(6, 3, 3)), E=np.arange(6.0), F=rng.normal(size=(6, 3, 3)),
             z=np.array([6, 1, 8]), lattice=np.eye(3) * 9)
    check_reader(NpzReader, path)


def test_a_writer_that_does_not_give_the_data_back_is_named(tmp_path):
    class Lossy(NpzReader):
        name = "lossy"

        @classmethod
        def open_writer(cls, path, contents, format=None):
            inner = NpzReader.open_writer(path, contents, format)

            class W:
                skipped = 0

                def write(self, chunk):
                    chunk = dict(chunk)
                    chunk["positions"] = chunk["positions"] + 1
                    inner.write(chunk)

                def close(self):
                    inner.close()
            return W()

    path = str(tmp_path / "d.npz")
    np.savez(path, R=np.zeros((4, 3, 3)), E=np.arange(4.0), F=np.zeros((4, 3, 3)), z=np.array([6, 1, 8]))
    with pytest.raises(AssertionError, match="positions.*written and read back"):
        check_reader(Lossy, path)


# ── writing a dataset ───────────────────────────────────────────────────────

def test_a_dataset_is_written_frame_by_frame_with_its_own_elements_and_reads_back(tmp_path):
    src = _write(tmp_path, _frames(5, variable=True), "src.traj")
    ds = _dataset(src, AseReader)
    out = str(tmp_path / "out.extxyz")
    skipped = write_dataset(AseReader, out, ds, [_reference_item(ds)], format="extxyz")
    assert skipped == 0
    back = ase.io.read(out, index=":")
    assert [len(a) for a in back] == [5, 8, 5, 8, 5]
    assert [a.get_chemical_formula() for a in back][:2] == ["CH4", "C2H6"]
    assert back[1].get_potential_energy() == ds.getEnergies(1)
    assert np.allclose(back[2].get_forces(), ds.getForces(2))


def test_a_prediction_is_written_beside_the_reference_and_loads_back_as_a_second_set(tmp_path):
    src = _write(tmp_path, _frames(4), "src.traj")
    ds = _dataset(src, AseReader)
    pred = ExportItem(
        energy_name="MACE_energy", force_name="MACE_forces",
        energy=lambda i: -100.0 - i,
        forces=lambda i: np.full((len(ds.getElements(i)), 3), 0.5 + i))
    out = str(tmp_path / "both.extxyz")
    write_dataset(AseReader, out, ds, [_reference_item(ds), pred], format="extxyz")
    contents = AseReader(out).contents()
    assert "MACE_energy" in contents["energies"] and "energy" in contents["energies"]
    assert "MACE_forces" in contents["forces"] and "forces" in contents["forces"]
    got = AseReader(out).read([0, 1, 2, 3])
    assert got["energy:MACE_energy"].tolist() == [-100.0, -101.0, -102.0, -103.0]
    assert got["energy:energy"].tolist() == [ds.getEnergies(i) for i in range(4)]
    # and as a dataset it gives the extra names as predictions to add
    again = ReaderDataset(out, chunks.read_arrays(AseReader, out), "ase (auto)")
    assert {"MACE_energy", "energy"} <= set(again.contents["energies"])


def test_frames_with_no_atoms_are_left_out_and_counted(tmp_path):
    class Gappy:
        isVariable = True

        def getN(self):
            return 3

        def getElements(self, i):
            return np.array([6, 1]) if i != 1 else np.array([], dtype=int)

        def getCoordinates(self, i):
            return np.zeros((2 if i != 1 else 0, 3))

        def getLattice(self, indices=None):
            return None

        def hasForces(self):
            return False

        def getEnergies(self, i):
            return -1.0 * i

    out = str(tmp_path / "g.extxyz")
    skipped = write_dataset(AseReader, out, Gappy(), [ExportItem("energy", "forces", lambda i: -1.0 * i, None)],
                            format="extxyz")
    assert skipped == 1
    assert len(ase.io.read(out, index=":")) == 2


def test_the_chunks_are_written_one_after_another(tmp_path):
    src = _write(tmp_path, _frames(7), "src.traj")
    ds = _dataset(src, AseReader)
    seen = []
    out = str(tmp_path / "c.extxyz")
    write_dataset(AseReader, out, ds, [_reference_item(ds)], format="extxyz", chunk=3,
                  progress=lambda done, total: seen.append((done, total)))
    assert seen == [(3, 7), (6, 7), (7, 7)]
    assert len(ase.io.read(out, index=":")) == 7


# ── npz ─────────────────────────────────────────────────────────────────────

def test_sgdml_refuses_a_dataset_whose_structures_differ_and_a_second_set(tmp_path):
    src = _write(tmp_path, _frames(4, variable=True), "v.traj")
    ds = _dataset(src, AseReader)
    with pytest.raises(ReaderError, match="An sGDML file needs the same atoms in the same order "
                                          "in every structure, and this dataset's structures differ."):
        write_dataset(NpzReader, str(tmp_path / "o.npz"), ds, [_reference_item(ds)], format="sgdml")
    src = _write(tmp_path, _frames(4), "u.traj")
    ds = _dataset(src, AseReader)
    second = ExportItem("P_energy", "P_forces", lambda i: 0.0, lambda i: np.zeros((5, 3)))
    with pytest.raises(ReaderError, match="sGDML holds 1 set of energies and forces; 2 are chosen"):
        write_dataset(NpzReader, str(tmp_path / "o2.npz"), ds, [_reference_item(ds), second], format="sgdml")


def test_sgdml_writes_the_old_arrays_and_reads_back(tmp_path):
    src = _write(tmp_path, _frames(4), "u.traj")
    ds = _dataset(src, AseReader)
    out = str(tmp_path / "o.npz")
    write_dataset(NpzReader, out, ds, [_reference_item(ds)], format="sgdml")
    with np.load(out, allow_pickle=True) as z:
        assert z["R"].shape == (4, 5, 3) and z["F"].shape == (4, 5, 3) and z["E"].shape == (4,)
        assert z["z"].tolist() == [6, 1, 1, 1, 1] and "md5" in z.files
    back = ReaderDataset(out, chunks.read_arrays(NpzReader, out), "sGDML")
    assert np.array_equal(back.R_flat, ds.R_flat) and np.array_equal(back.E, ds.E)


# ── the plan's checks, for the dialog and the server alike ──────────────────

def test_check_writable_names_what_a_format_cannot_hold():
    class Var:
        isVariable = True

        def getN(self):
            return 3

    only_one = {"frames": 1, "energies": 0, "forces": 0}
    with pytest.raises(ReaderError, match="holds one structure; this dataset has 3"):
        check_writable({"label": "VASP POSCAR", "can_write": only_one}, Var(), [])
    with pytest.raises(ReaderError, match="holds no energies or forces"):
        check_writable({"label": "VASP POSCAR", "can_write": {"energies": 0, "forces": 0}}, type(
            "One", (), {"isVariable": False, "getN": lambda self: 1})(), [ExportItem("e", "f", lambda i: 0.0, None)])


def _one(n=2):
    return type("One", (), {"isVariable": False, "getN": lambda self: n})()


def test_check_writable_refuses_more_force_sets_than_the_format_holds():
    traj = {"label": "ASE trajectory", "can_write": {"forces": 1}}
    both = [ExportItem("e", "f", lambda i: 0.0, lambda i: np.zeros((1, 3))),
            ExportItem("m_e", "m_f", lambda i: 0.0, lambda i: np.zeros((1, 3)))]
    with pytest.raises(ReaderError, match="ASE trajectory holds 1 set of forces; 2 are chosen"):
        check_writable(traj, _one(), both)
    # a set with an energy only takes no room for forces
    check_writable(traj, _one(), [both[0], ExportItem("m_e", "m_f", lambda i: 0.0, None)])


def test_a_format_with_no_forces_writes_the_energies_and_leaves_the_forces_out(tmp_path):
    written = {}

    class NoForces:
        name = "noforces"
        write_formats = [{"format": "nf", "label": "No forces", "ext": ".nf",
                          "can_write": {"energies": 1, "forces": 0}}]

        @staticmethod
        def open_writer(path, contents, format):
            written["contents"] = contents
            written["chunks"] = []
            return type("W", (), {"write": lambda self, c: written["chunks"].append(c),
                                  "close": lambda self: None})()

    src = _write(tmp_path, _frames(3), "src.traj")
    ds = _dataset(src, AseReader)
    write_dataset(NoForces, str(tmp_path / "out.nf"), ds, [_reference_item(ds)], format="nf")
    assert written["contents"]["energies"] == ["energy"] and written["contents"]["forces"] == []
    assert "energy:energy" in written["chunks"][0]
    assert not any(k.startswith("forces:") for k in written["chunks"][0])



class ToyTextWritten(ToyTextReader):
    """A Level 2 reader (ToyTextReader) with a writer of its own lines.
    Module level: check_reader pickles a Level 2 reader."""

    name = "toytext-writer"
    write_formats = [{"format": "toyt", "label": "Toy text", "ext": ".toyt", "can_write": {}}]
    shift = 0.0                      # the writer moves every position by this

    @classmethod
    def open_writer(cls, path, contents, format=None):
        out = open(path, "w")
        shift = cls.shift

        class W:
            skipped = 0

            def write(self, chunk):
                first = 0
                for i, n in enumerate(chunk["n_atoms"]):
                    rows = slice(first, first + int(n))
                    out.write(json.dumps({
                        "numbers": chunk["numbers"][rows].tolist(),
                        "positions": (chunk["positions"][rows] + shift).tolist(),
                        "energy": float(chunk["energy:dft"][i]),
                        "forces": chunk["forces:dft"][rows].tolist()}) + "\n")
                    first += int(n)

            def close(self):
                out.close()
        return W()


class ToyTextShifted(ToyTextWritten):
    name = "toytext-shifted"
    shift = 1.0


def test_a_level2_readers_writer_is_round_tripped_too(tmp_path):
    path = write_toy(tmp_path / "d.toyt", make_frames(4, variable=True))
    check_reader(ToyTextWritten, path)
    with pytest.raises(AssertionError, match="positions.*written and read back"):
        check_reader(ToyTextShifted, path)


def test_a_dataset_with_no_structures_is_refused_plainly(tmp_path):
    empty = type("Empty", (), {"isVariable": False, "getN": lambda self: 0,
                               "getLattice": lambda self, *a: None})()
    with pytest.raises(ReaderError, match="This dataset has no structures to write"):
        write_dataset(NpzReader, str(tmp_path / "e.npz"), empty, [], format="npz")
