"""The fast parser (``ffast_xyz``, ADR 0100) gives the Python parser's arrays,
bit for bit and number type for number type, or hands the file over.

Each case is read twice through ``read_level2``: with the Python parser only,
then with the fast parser (1 thread, 4 threads, groups of one frame). Both
must give the same arrays, or both the same error. A case in ``FAST`` must be
read by the fast parser itself, not handed over."""
import logging
from pathlib import Path

import numpy as np
import pytest

pytest.importorskip("ffast_xyz", reason="the fast parser (rust/ffast-xyz) is not installed")

from ffast.io import frame_cache, frame_index  # noqa: E402
from ffast.readers import chunks, text  # noqa: E402
from ffast.readers.xyz_reader import XyzReader  # noqa: E402
from tests.ffast.read_once_fixtures import FILES  # noqa: E402

HEAD = 'Properties=species:S:1:pos:R:3:forces:R:3 energy={e} pbc="F F F"'


def two_frames(pos="0.5", energy="-5.25", header=HEAD, species="H", sep=" ", nl="\n"):
    lines = []
    for f in range(2):
        lines += ["1", header.format(e=energy),
                  sep.join([species, pos, "0.0", str(f), "0.1", "0.2", "0.3"])]
    return nl.join(lines) + nl


def frames(n, header, atom, atoms=2):
    """``n`` frames of ``atoms`` atoms; ``header(i)`` and ``atom(i, a)`` give the lines."""
    return "".join(f"{atoms}\n{header(i)}\n" + "".join(atom(i, a) + "\n" for a in range(atoms))
                   for i in range(n))


CASES = {}
# Numbers in atom lines (from the prototype's diff_edges.py).
for tok in ["1.5", ".5", "5.", "1e5", "1E+05", "+1.0", "-0.0", "1.0e-320", "1e400", "inf", "-inf",
            "nan", "NaN", "+NaN", "-nan", "Infinity", "+Inf", "-INFINITY", "1_000.5", "0x1p3", "1.0d0", "1,5",
            "0.1000000000000000055511151231257827021181583404541015625", "１.5"]:
    CASES[f"atom number {tok!r}"] = two_frames(pos=tok)
# Numbers in headers.
for tok in ["-5", "007", "+3", "-0", "1_000", "99999999999999999999", "1e3", "inf", "nan", "0x10",
            "1.0d0", '"-5"', "{-5}", "True", "-5.0", '" 5 "', "-nan", "1_0.5"]:
    CASES[f"energy {tok!r}"] = two_frames(energy=tok)
for sym in ["Cl", "CL", "cl", "X", "D", "Xx"]:
    CASES[f"species {sym!r}"] = two_frames(species=sym)
CASES["tab between columns"] = two_frames(sep="\t")
CASES["CRLF line ends"] = two_frames(nl="\r\n")
CASES["trailing spaces"] = two_frames(pos="0.5   ")
CASES["escaped quote in a value"] = two_frames(header=HEAD + ' note="a \\" b"')
CASES["escaped number in quotes"] = two_frames(header=HEAD + ' gap="1\\.5"')
CASES["single-quoted value"] = two_frames(header=HEAD + " note='a b'")
CASES["flag without value"] = two_frames(header=HEAD + " relaxed")
CASES["uid key"] = two_frames(header=HEAD + " uid=5 UID=6")
CASES["_JSON value"] = two_frames(header=HEAD + ' data="_JSON {{\\"a\\": 1}}"')
CASES["Lattice with commas"] = two_frames(
    header='Properties=species:S:1:pos:R:3:forces:R:3 energy={e} Lattice="5,0,0,0,5,0,0,0,5"')
CASES["Lattice, no pbc"] = two_frames(
    header='Properties=species:S:1:pos:R:3:forces:R:3 energy={e} Lattice="5 0 0 0 5 0 0 0 5.5"')
CASES["Lattice not 9 numbers"] = two_frames(
    header='Properties=species:S:1:pos:R:3:forces:R:3 energy={e} Lattice="5 0 0 0 5 0 0 0"')
CASES["Lattice with a word"] = two_frames(
    header='Properties=species:S:1:pos:R:3:forces:R:3 energy={e} Lattice="5 0 0 0 5 0 0 0 x"')
CASES["pbc single T"] = two_frames(header='Properties=species:S:1:pos:R:3:forces:R:3 energy={e} pbc=T')
CASES["pbc of two flags"] = two_frames(header='Properties=species:S:1:pos:R:3:forces:R:3 energy={e} pbc="T F"')
CASES["Lattice in braces"] = two_frames(
    header='Properties=species:S:1:pos:R:3:forces:R:3 energy={e} Lattice={{5 0 0 0 5 0 0 0 5}}')
CASES["int header field"] = two_frames(header=HEAD + " charge=2")
CASES["header field only in frame 0"] = two_frames().replace("energy=-5.25", "energy=-5.25 gap=1.5", 1)
CASES["Z column instead of species"] = "".join(
    f"1\nProperties=Z:I:1:pos:R:3 energy=-1\n1 0.5 0.0 {f}\n" for f in range(2))
CASES["Z and species"] = "".join(
    f"1\nProperties=species:S:1:pos:R:3:Z:I:1 energy=-1\nH 0.5 0.0 {f} 1\n" for f in range(2))
CASES["int per-atom column"] = "".join(
    f"1\nProperties=species:S:1:pos:R:3:tag:I:1 energy=-1\nH 0.5 0.0 {f} 3\n" for f in range(2))
CASES["species not first"] = "".join(
    f"1\nProperties=pos:R:3:species:S:1 energy=-1\n0.5 0.0 {f} H\n" for f in range(2))
CASES["plain xyz comment"] = "".join(f"1\nwater frame {f}\nH 0.5 0.0 {f}\n" for f in range(2))
CASES["plain comment, non-ASCII"] = "".join(f"1\nwater α {f}\nH 0.5 0.0 {f}\n" for f in range(2))
CASES["plain comment with the word pbc"] = "".join(f"1\nno pbc here {f}\nH 0.5 0.0 {f}\n" for f in range(2))
# Header keys (from the prototype's diff_headers.py).
for name, tok in {
    "hyphen in key": "a-b=1", "key starts with digit": "1a=2", "empty key": "=5",
    "duplicate key": "charge=1 charge=2", "duplicate key, then a word": "charge=1 charge=bulk",
    "duplicate key, then text": "charge=bulk charge=1_0", "unclosed quote": 'note="abc',
    "list of numbers": 'stress="1 2 3 4 5 6 7 8 9"', "bool value": "relaxed=T",
    "string value": "config_type=bulk", "quoted number": 'gap="1.5"', "bracket list": "ids=[1,2]",
    "lowercase properties key": "properties=species:S:1:pos:R:3",
    "value then text": 'note="a"b=2', "unicode in quoted value": 'note="Å β"',
    "unicode in a bare value": "note=Åβ", "unicode key": "é=1",
    "very long quoted value": 'note="' + "x " * 2000 + '"', "tab between pairs": "\tcharge=3",
    "key with nothing after =": "charge=", "backslash at the end": 'note="a\\',
    "free_energy and dft_energy": "free_energy=-1 dft_energy=-2",
}.items():
    CASES[name] = two_frames(header=HEAD + " " + tok)
CASES["Properties last"] = two_frames(header='energy={e} pbc="F F F" Properties=species:S:1:pos:R:3:forces:R:3')
CASES["Properties without value"] = two_frames(header="Properties energy={e}")
# Columns.
CASES["an optional column some frames lack"] = frames(
    6, lambda i: "Properties=species:S:1:pos:R:3" + (":q:R:1" if i % 2 else "") + f" energy={i}",
    lambda i, a: f"H {a}.0 0.0 {i}.0" + (f" {a + 0.5}" if i % 2 else ""))
CASES["an int column, then a real one"] = frames(
    4, lambda i: "Properties=species:S:1:pos:R:3:q:" + ("I" if i < 2 else "R") + ":1 energy=1",
    lambda i, a: f"H {a}.0 0.0 {i}.0 " + (str(a) if i < 2 else f"{a}.5"))
CASES["the forces column changes"] = frames(
    4, lambda i: "Properties=species:S:1:pos:R:3" + (":forces:R:3" if i < 2 else "") + " energy=1",
    lambda i, a: f"H {a}.0 0.0 {i}.0" + (" 0.1 0.2 0.3" if i < 2 else ""))
CASES["a force column of ints"] = frames(
    2, lambda i: "Properties=species:S:1:pos:R:3:my_forces:I:3 energy=1", lambda i, a: f"H {a}.0 0 {i}.0 1 2 3")
CASES["a column of type L"] = frames(
    2, lambda i: "Properties=species:S:1:pos:R:3:fixed:L:1 energy=1", lambda i, a: f"H {a}.0 0 {i}.0 T")
CASES["a wide column nothing keeps"] = frames(
    2, lambda i: "Properties=species:S:1:pos:R:3:momenta:R:3:extra:R:2 energy=1",
    lambda i, a: f"H {a}.0 0 {i}.0 1 2 3 4 5")
CASES["a word in a column nothing keeps"] = frames(
    2, lambda i: "Properties=species:S:1:pos:R:3:momenta:R:3 energy=1", lambda i, a: f"H {a}.0 0 {i}.0 1 x 3")
CASES["an atom line one column short"] = frames(
    2, lambda i: "Properties=species:S:1:pos:R:3 energy=1", lambda i, a: f"H {a}.0 0" + ("" if a else f" {i}.0"))
CASES["energies and fields, ints and floats"] = frames(
    5, lambda i: f"energy={i} dft_energy={i * 1.5} charge={i % 2} gap={'x' if i == 3 else i}"
                 f" Properties=species:S:1:pos:R:3:forces:R:3:mace_forces:R:3:weight:R:1",
    lambda i, a: f"H {a}.0 0 {i}.0 0.1 0.2 0.3 1 2 3 {a * 0.25}")
# A field first seen after the 100 frames contents() looks at. (A 3-wide
# column cannot start later: every frame must list frame 0's wide columns.)
CASES["a column that starts after frame 100"] = frames(
    104, lambda i: "Properties=species:S:1:pos:R:3" + (":late:R:1" if i >= 102 else "") + f" energy={i}",
    lambda i, a: f"H {a}.0 0 {i}.0" + (f" {i}.5" if i >= 102 else ""), atoms=1)
CASES["missing optional biomolecule values"] = frames(
    3,
    lambda i: 'Properties=species:S:1:pos:R:3:forces:R:3:forces_so3lr:R:3:hirshfeld_ratios:R:1 '
              'energy=-148565.0 energy_so3lr=-358.178 dipole_vec="nan nan nan" pbc="F F F"',
    lambda i, a: f"C {a}.0 0 {i}.0 0.1 0.2 0.3 -0.1 0.2 0.3 nan")

# Read by the fast parser itself, not handed over.
FAST = {
    "missing optional biomolecule values",
    *(f"atom number {tok!r}" for tok in
      ["nan", "NaN", "+NaN", "-nan", "inf", "-inf", "Infinity", "+Inf", "-INFINITY"]),
    "atom number '1.5'", "atom number '.5'", "atom number '5.'", "atom number '1e5'",
    "atom number '-0.0'", "atom number '1.0e-320'", "atom number '1e400'",
    "atom number '0.1000000000000000055511151231257827021181583404541015625'",
    "atom number '+1.0'", "atom number '1E+05'",
    "energy '-5'", "energy '007'", "energy '1_000'", "energy '99999999999999999999'", "energy 'nan'",
    "energy '\"-5\"'", "energy 'True'", "energy '\" 5 \"'", "energy '+3'", "energy '-0'",
    "energy '-5.0'", "energy '1e3'", "energy '{-5}'", "energy 'inf'", "energy '-nan'", "energy '0x10'",
    "energy '1.0d0'", "energy '1_0.5'",
    "species 'cl'", "species 'X'", "species 'Cl'", "species 'CL'",
    "tab between columns", "CRLF line ends", "trailing spaces", "escaped quote in a value",
    "escaped number in quotes", "single-quoted value", "flag without value", "uid key", "_JSON value",
    "Lattice with commas", "Lattice, no pbc", "pbc single T", "Lattice in braces", "int header field",
    "header field only in frame 0", "Z column instead of species", "int per-atom column",
    "species not first", "plain xyz comment", "plain comment, non-ASCII", "duplicate key",
    "duplicate key, then a word", "list of numbers", "bool value", "string value", "quoted number",
    "bracket list", "lowercase properties key", "unicode in quoted value", "very long quoted value",
    "tab between pairs", "free_energy and dft_energy", "Properties last",
    "an optional column some frames lack",
    "a wide column nothing keeps", "energies and fields, ints and floats",
    "a column that starts after frame 100", "uniform.extxyz", "variable.extxyz", "plain.xyz",
}

EXAMPLES = sorted(p for p in (Path(__file__).resolve().parents[3] / "examples" / "data").rglob("*")
                  if p.suffix in (".xyz", ".extxyz"))


@pytest.fixture(autouse=True)
def _fresh(tmp_path, monkeypatch):
    monkeypatch.setattr(frame_index, "CACHE_DIR", tmp_path / "index")
    monkeypatch.setattr(frame_cache, "CACHE_DIR", tmp_path / "arrays")
    text.clear_memory_cache()
    yield
    text.clear_memory_cache()


def _read(path, fast, monkeypatch, **kw):
    monkeypatch.setattr(text, "fast_parser_usable", lambda: fast)
    text.clear_memory_cache()
    try:
        return chunks.read_arrays(XyzReader, str(path), use_cache=False, **kw).arrays
    except Exception as exc:
        return exc


def _bits(a):
    a = np.ascontiguousarray(a)
    return a.view(np.uint64) if a.dtype == np.float64 else a


def _same(fast, python, label):
    if isinstance(python, Exception) or isinstance(fast, Exception):
        assert type(fast) is type(python) and str(fast) == str(python), (label, fast, python)
        return
    assert sorted(fast) == sorted(python), label
    for key in python:
        a, b = np.asarray(fast[key]), np.asarray(python[key])
        assert a.dtype == b.dtype and a.shape == b.shape, (label, key, a.dtype, b.dtype, a.shape, b.shape)
        if a.dtype == object:
            assert a.tolist() == b.tolist(), (label, key)
        else:
            assert np.array_equal(_bits(a), _bits(b)), (label, key, a.ravel()[:6], b.ravel()[:6])


def _check(path, label, monkeypatch, caplog):
    """Against the Python parser at the same settings: it parses in batches
    and checks a frame's columns against its batch's first frame only, so a
    file whose columns change can read with one batch size and not another."""
    for kw in ({"workers": 1}, {"workers": 4}, {"workers": 4, "batch": 1}):
        python = _read(path, False, monkeypatch, **kw)
        caplog.clear()
        with caplog.at_level(logging.WARNING, logger="FFAST"):
            fast = _read(path, True, monkeypatch, **kw)
        handed = "slower Python parser" in caplog.text
        _same(fast, python, (label, kw))
        if label in FAST:
            assert not handed, (label, caplog.text)


@pytest.mark.parametrize("name", sorted(CASES))
def test_an_edge_case(name, tmp_path, monkeypatch, caplog):
    path = tmp_path / "case.extxyz"
    path.write_bytes(CASES[name].encode())
    _check(path, name, monkeypatch, caplog)


@pytest.mark.parametrize("name", sorted(FILES))
def test_a_read_once_fixture(name, tmp_path, monkeypatch, caplog):
    path = tmp_path / name
    path.write_text(FILES[name])
    _check(path, name, monkeypatch, caplog)


@pytest.mark.parametrize("path", EXAMPLES, ids=[p.name for p in EXAMPLES])
def test_an_example_file(path, monkeypatch, caplog):
    _check(path, path.name, monkeypatch, caplog)
    caplog.clear()
    with caplog.at_level(logging.WARNING, logger="FFAST"):
        _read(path, True, monkeypatch, workers=4)
    assert "slower Python parser" not in caplog.text, caplog.text


def test_a_stride_reads_the_same_frames(monkeypatch, caplog):
    if not EXAMPLES:
        pytest.skip("no example files in examples/data")
    path = max(EXAMPLES, key=lambda p: p.stat().st_size)
    python = _read(path, False, monkeypatch, stride=3, workers=1)
    _same(_read(path, True, monkeypatch, stride=3, workers=4), python, "stride")
    _same(_read(path, True, monkeypatch, stride=3, workers=2, batch=5),
          _read(path, False, monkeypatch, stride=3, workers=2, batch=5), "stride, groups of 5")


@pytest.mark.parametrize("stride", [1, 3, 25])
def test_frames_far_apart_are_read_in_runs_of_their_own(stride, tmp_path, monkeypatch, caplog):
    # Frames of about 75 KB: a stride of 25 leaves gaps over 1 MB (each kept
    # frame read on its own), a stride of 3 gaps under it (read through).
    path = tmp_path / "big.extxyz"
    path.write_text(frames(120, lambda i: f'Properties=species:S:1:pos:R:3:forces:R:3 energy={-i * 0.5} pbc="F F F"',
                           lambda i, a: f"H {a * 0.1:.6f} {i}.25 -1.5 0.01 0.02 {a}e-3", atoms=1500))
    python = _read(path, False, monkeypatch, stride=stride, workers=1)
    caplog.clear()
    with caplog.at_level(logging.WARNING, logger="FFAST"):
        fast = _read(path, True, monkeypatch, stride=stride, workers=4, batch=2)
    assert "slower Python parser" not in caplog.text
    _same(fast, python, ("far apart", stride))


def test_the_parser_writes_nan_into_every_row_of_a_frame_that_lacks_a_column(tmp_path):
    """FFAST makes the arrays with np.empty when FILLS_MISSING is set, so
    the parser must write the rows of frames without the column itself."""
    import ffast_xyz

    from ffast.io import frame_index

    assert ffast_xyz.FILLS_MISSING and XyzReader.fills_missing()
    path = tmp_path / "lacks.extxyz"
    path.write_bytes(CASES["an optional column some frames lack"].encode())   # frames 0, 2, 4 lack q
    XyzReader(str(path)).contents()
    index = frame_index.scan(str(path))
    starts = np.asarray(index.starts[:-1] if len(index.starts) > len(index.counts) else index.starts, np.uint64)
    ends = np.append(starts[1:], path.stat().st_size).astype(np.uint64)
    counts = np.asarray(index.counts, np.int64)
    atoms = int(counts.sum())
    arrays = {"numbers": np.zeros(atoms, np.int64), "positions": np.full((atoms, 3), 7.0),
              "atom:q": np.full(atoms, 7.0)}                       # 7.0: what np.empty might hold
    got = XyzReader.parse_into(str(path), starts, ends, counts, 0, arrays, 2)
    assert got["filled"] == ["atom:q"]
    lacking = np.repeat([i % 2 == 0 for i in range(len(counts))], counts)
    assert lacking.any() and (~lacking).any()
    assert np.isnan(arrays["atom:q"][lacking]).all()
    assert not np.isnan(arrays["atom:q"][~lacking]).any()


def test_a_parser_build_without_the_flag_still_gets_nan_filled_arrays(tmp_path, monkeypatch, caplog):
    """An older ffast_xyz leaves rows of frames without a column untouched;
    FFAST then fills the arrays with NaN first, as before."""
    monkeypatch.setattr(XyzReader, "fills_missing", staticmethod(lambda: False))
    path = tmp_path / "old.extxyz"
    path.write_bytes(CASES["an optional column some frames lack"].encode())
    _check(path, "an optional column some frames lack", monkeypatch, caplog)
