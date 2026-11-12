"""A Level 2 reader may list each frame's energies in a pass of their own
(ADR 0096, decision 1): the xyz reader reads them off the header lines only,
FFAST splits the pass across workers and saves the result next to the frame
list, and ``check_reader`` holds them to what ``parse`` returns."""
import numpy as np
import pytest

from ffast.io import extxyz_arrays as xa
from ffast.io import frame_cache, frame_index as fi
from ffast.readers import check_reader, text
from ffast.readers.xyz_reader import XyzReader
from tests.ffast.readers.toy_reader import ToyTextReader, make_frames, write_toy


@pytest.fixture(autouse=True)
def _fresh(tmp_path, monkeypatch):
    monkeypatch.setattr(fi, "CACHE_DIR", tmp_path / "index")
    monkeypatch.setattr(frame_cache, "CACHE_DIR", tmp_path / "arrays")
    fi.clear_memory_cache()
    text.clear_memory_cache()
    ToyTextReader.found, ToyTextReader.parsed = [], []
    ENERGY_CALLS.clear()
    yield
    fi.clear_memory_cache()
    text.clear_memory_cache()


def _xyz(tmp_path, headers, atoms=2, name="a.xyz"):
    """One frame per header line; each frame has ``atoms`` atoms."""
    lines = []
    for i, header in enumerate(headers):
        lines.append(str(atoms))
        lines.append(header)
        lines += [f"H {i + a} 0.0 0.0" for a in range(atoms)]
    path = tmp_path / name
    path.write_text("\n".join(lines) + "\n")
    return str(path)


_PROPS = 'Properties=species:S:1:pos:R:3'
HEADERS = [
    f'energy=-1.5 {_PROPS}',
    f'energy=-2.5 mace_energy=-9 {_PROPS}',
    f'{_PROPS}',                                  # no energy at all
    f'energy=-4.5 free_energy=7 {_PROPS}',
    f'energy=nan_text {_PROPS}',                   # not a number
]


def _starts(path):
    return text.get_table(XyzReader, path)[0][:-1]


def test_the_xyz_reader_reads_the_header_energies_with_nan_where_a_frame_has_none(tmp_path):
    path = _xyz(tmp_path, HEADERS)
    energies = XyzReader.energies(path, _starts(path))
    assert set(energies) == {"energy", "mace_energy"}          # free_energy is not an energy name
    np.testing.assert_array_equal(energies["energy"], [-1.5, -2.5, np.nan, -4.5, np.nan])
    np.testing.assert_array_equal(energies["mace_energy"], [np.nan, -9, np.nan, np.nan, np.nan])
    assert energies["energy"].dtype == np.float64


def test_the_xyz_reader_reads_only_the_frames_asked_for(tmp_path):
    path = _xyz(tmp_path, HEADERS)
    energies = XyzReader.energies(path, _starts(path)[[1, 3]])
    np.testing.assert_array_equal(energies["energy"], [-2.5, -4.5])
    np.testing.assert_array_equal(energies["mace_energy"], [-9, np.nan])


def test_a_file_with_no_energy_gives_an_empty_dict(tmp_path):
    path = _xyz(tmp_path, [_PROPS, _PROPS])
    assert XyzReader.energies(path, _starts(path)) == {}


def test_a_plain_comment_line_holds_no_energy(tmp_path):
    path = _xyz(tmp_path, ["total energy of this frame", "energy"])
    assert XyzReader.energies(path, _starts(path)) == {}


TRICKY = [
    b'energy="1.5" a=1',
    b"x='a energy=3' energy=2",
    b'c="foo energy=3" Energy=4 free_energy=5',
    b'e_energy=1e3 energy_x=-2 energy=inf',
    b'energy=abc', b'energy', b'k=energy', b'energy=7', b'energy=1 energy=2',
    b'a="b\\" energy=7" energy=8',
    b'w="_JSON [\\"x energy=1\\", \\"y]\\"]" energy=9 homo_energy=-3',
    b'w="_JSON {\\"k\\": \\"energy=2\\"}" nl_energy=4',
    b'comment="it\'s" energy=6',
    b'\tenergy=4', b'nrg=1 ENERGY=2 energy=[1 2]', b'energy=_JSON 3',
    b"mace_energy=-9 energy=-2.5 Properties=species:S:1:pos:R:3",
    b'energy=12 pbc="F F F" Lattice="1 0 0 0 1 0 0 0 1"',
]


@pytest.mark.parametrize("header", TRICKY)
def test_the_quick_header_read_agrees_with_the_full_header_parse(header):
    assert xa.header_energies(header) == xa.header_energies_full(header)


def test_the_energies_agree_with_what_parse_returns(tmp_path):
    path = _xyz(tmp_path, [h for h in HEADERS if "nan_text" not in h])
    check_reader(XyzReader, path)
    energies = XyzReader.energies(path, _starts(path))
    with open(path, "rb") as f:
        parsed = XyzReader.parse(f.read(), 0)
    for name, values in energies.items():
        np.testing.assert_array_equal(values, parsed[f"energy:{name}"])


def test_a_second_load_reads_no_energies(tmp_path, monkeypatch):
    path = _xyz(tmp_path, HEADERS)
    calls = []
    real = XyzReader.energies
    monkeypatch.setattr(XyzReader, "energies", staticmethod(lambda p, s: calls.append(1) or real(p, s)))
    first = text.get_energies(XyzReader, path)
    fi.clear_memory_cache()
    text.clear_memory_cache()                  # as in a new session: only the disk copy is left
    second = text.get_energies(XyzReader, path)
    assert len(calls) == 1
    assert set(first) == set(second) == {"energy", "mace_energy"}
    for name in first:
        np.testing.assert_array_equal(first[name], second[name])


def test_the_saved_energies_follow_the_file(tmp_path):
    path = _xyz(tmp_path, HEADERS)
    assert text.get_energies(XyzReader, path)["energy"][0] == -1.5
    text.clear_memory_cache()
    fi.clear_memory_cache()
    _xyz(tmp_path, [f'energy=-7 {_PROPS}', f'energy=-8 {_PROPS}', f'energy=-8.5 {_PROPS}'])
    np.testing.assert_array_equal(text.get_energies(XyzReader, path)["energy"], [-7, -8, -8.5])


def test_workers_give_the_same_energies(tmp_path, monkeypatch):
    headers = [f'energy={-i * 0.5} {"mace_energy=" + str(i) if i % 3 else ""} {_PROPS}' for i in range(40)]
    path = _xyz(tmp_path, headers)
    alone = text.get_energies(XyzReader, path, workers=1)
    text.clear_memory_cache()
    monkeypatch.setattr(text, "ENERGY_PARALLEL_MIN_FRAMES", 0)
    split = text.get_energies(XyzReader, path, workers=3, use_cache=False)
    assert set(alone) == set(split) == {"energy", "mace_energy"}
    for name in alone:
        np.testing.assert_array_equal(alone[name], split[name])
    np.testing.assert_array_equal(split["energy"], [-i * 0.5 for i in range(40)])


def test_a_reader_without_energies_gives_none(tmp_path):
    path = write_toy(tmp_path / "a.toyt", make_frames(4))
    assert text.get_energies(ToyTextReader, path) is None


ENERGY_CALLS = []


def _toy_energies(path, starts):
    """Each toy line's ``energy`` (it is the frame number times 10)."""
    ENERGY_CALLS.append(len(starts))
    import json
    out = np.full(len(starts), np.nan)
    with open(path, "rb") as f:
        for i, s in enumerate(starts.tolist()):
            f.seek(s)
            out[i] = json.loads(f.readline()).get("energy", np.nan)
    return {"dft": out}


class _WithEnergies(ToyTextReader):
    name = "toyenergies"
    extensions = ["*.toye"]
    energies = staticmethod(_toy_energies)


def test_a_reader_may_list_energies_and_they_are_kept_next_to_its_frame_list(tmp_path):
    path = write_toy(tmp_path / "a.toye", make_frames(6))
    check_reader(_WithEnergies, path)
    ENERGY_CALLS.clear()
    got = text.get_energies(_WithEnergies, path)
    np.testing.assert_array_equal(got["dft"], np.arange(6) * 10.0)
    text.clear_memory_cache()
    again = text.get_energies(_WithEnergies, path)
    np.testing.assert_array_equal(again["dft"], got["dft"])
    assert ENERGY_CALLS == [6]                 # the second answer came from the saved copy


def _disagree(path, starts):
    out = _toy_energies(path, starts)
    out["dft"][out["dft"] == 20.0] += 1.0     # frame 2, wherever it is asked for
    return out


def _short(path, starts):
    return {"dft": _toy_energies(path, starts)["dft"][:-1]}


def _unknown(path, starts):
    out = _toy_energies(path, starts)
    return {**out, "ghost": out["dft"]}


def _depends_on_company(path, starts):
    out = _toy_energies(path, starts)
    out["dft"] += len(starts)                  # a frame's value changes with the frames asked with it
    return out


class _Disagree(_WithEnergies):
    name = "toydisagree"
    energies = staticmethod(_disagree)


class _Short(_WithEnergies):
    name = "toyshort"
    energies = staticmethod(_short)


class _Unknown(_WithEnergies):
    name = "toyunknown"
    energies = staticmethod(_unknown)


class _Company(_WithEnergies):
    name = "toycompany"
    energies = staticmethod(_depends_on_company)


@pytest.mark.parametrize("cls, message", [
    (_Disagree, "energies.*dft.*parse"),
    (_Short, "one value per frame"),
    (_Unknown, "ghost"),
    (_Company, "halves"),
])
def test_check_reader_names_energies_that_break_the_rules(tmp_path, cls, message):
    path = write_toy(tmp_path / "a.toye", make_frames(6))
    with pytest.raises(AssertionError, match=message):
        check_reader(cls, path)
