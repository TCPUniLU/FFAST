"""Energies for sampling with the fast parser (``ffast_xyz.energies``, ADR
0100 step 6) are ``XyzReader.energies``'s by the Python code: the same names
in the same order, the same floats bit for bit, NaN where a frame has none,
and the same error.

Each case is read with the Python code only, then with the fast parser on 1
and on 4 threads. Rust hands a header it cannot judge back to Python; a case
in ``RUST_ALONE`` must be read by Rust itself."""
from pathlib import Path

import numpy as np
import pytest

ffast_xyz = pytest.importorskip("ffast_xyz", reason="the fast parser (rust/ffast-xyz) is not installed")

from ffast.io import extxyz_arrays as xa  # noqa: E402
from ffast.io import frame_cache, frame_index  # noqa: E402
from ffast.readers import text  # noqa: E402
from ffast.readers.xyz_reader import XyzReader  # noqa: E402
from tests.ffast.read_once_fixtures import FILES  # noqa: E402
from tests.ffast.readers.test_energies import HEADERS, TRICKY  # noqa: E402
from tests.ffast.readers.test_fast_parser_parity import CASES  # noqa: E402

PROPS = "Properties=species:S:1:pos:R:3"
VALUES = {
    "whole": "-5", "zero": "0", "minus zero": "-0", "minus zero float": "-0.0", "plus": "+5",
    "leading zeros": "007", "trailing dot": "5.", "leading dot": ".5", "exponent": "1.5e-3",
    "big exponent": "1e400", "tiny": "1e-400", "20 digits": "99999999999999999999",
    "long mantissa": "0.1000000000000000055511151231257827021181583404541015625",
    "300 digits": "9" * 300, "nan": "nan", "inf": "-Infinity", "underscore": "1_000",
    "two signs": "+-5", "word": "PBE", "word with digits": "x5", "comma": "1,5",
    "hex": "0x10", "fortran": "1.0d0", "non-ASCII digit": "５", "vertical tab after": "5\x0b",
    "quoted": '"-5.5"', "quoted with spaces": '" 5 "', "single-quoted": "'3'", "quoted empty": '""',
    "quoted escape": '"1\\.5"', "unclosed quote": '"5', "braces": "{5}", "brackets": "[5]",
    "JSON": '"_JSON 5"', "empty": "",
}
RUST_ALONE = {"whole", "zero", "minus zero", "minus zero float", "plus", "leading zeros", "trailing dot",
              "leading dot", "exponent", "big exponent", "tiny", "20 digits", "long mantissa", "300 digits",
              "word", "word with digits", "comma", "hex", "fortran", "vertical tab after",
              "non-ASCII digit", "quoted", "single-quoted",
              "quoted empty", "quoted escape", "unclosed quote", "braces", "brackets", "JSON", "empty"}

HEADER_CASES = {f"energy {k}": [f"energy={v} {PROPS}"] * 3 for k, v in VALUES.items()}
HEADER_CASES.update({f"tricky {i}": [h.decode()] * 2 for i, h in enumerate(TRICKY)})
HEADER_CASES["test_energies HEADERS"] = HEADERS
HEADER_CASES.update({
    "400 digits (Python raises)": [f"energy={'9' * 400} {PROPS}"],
    "last value wins, first place kept": ["b_energy=1 energy=2 b_energy=3", "energy=4"],
    "first good value decides the place": ["b_energy=x energy=2 b_energy=3"],
    "names in the order they first appear": ["energy=1", "c_energy=2 energy=3", "a_energy=4 c_energy=5",
                                             "b_energy=6"],
    "a handed-over frame brings a name first": ["energy=1", "\tz_energy=2 energy=3", "a_energy=4 z_energy=5"],
    "a handed-over frame with a name already seen": ["a_energy=1", "a_energy=nan b_energy=2", "b_energy=3"],
    "every frame handed over": ["energy=nan", "energy=1_0 x_energy=inf"],
    "per-frame ASE property": ["free_energy=1 energy=2 stress=3"],
    "CRLF": ["energy=1\r", "energy=2\r"],
    "key with a dot": ["x.energy=1 energy=2"],
    "upper case": ["ENERGY=1 Energy=2 eNeRgY=3"],
    "no equals sign": ["total energy"],
    "energy inside a quoted value": ['note="energy=5" energy=6', "note='x \\' energy=1' energy=7"],
    "JSON with energy inside": ['w="_JSON {\\"energy\\": 1}" energy=2'],
    "lattice and pbc": ['Lattice="1 0 0 0 1 0 0 0 1" energy=-3.25 pbc="T T T" ' + PROPS],
    "non-ASCII elsewhere": ["note=Åβ energy=1 é=2"],
    "control byte in a value": ["energy=1\x1c"],
})


def _write(tmp_path, headers, nl="\n"):
    lines = []
    for i, header in enumerate(headers):
        lines += ["1", header, f"H {i} 0.0 0.0"]
    path = tmp_path / "case.xyz"
    path.write_bytes((nl.join(lines) + nl).encode())
    return path


@pytest.fixture(autouse=True)
def _fresh(tmp_path, monkeypatch):
    monkeypatch.setattr(frame_index, "CACHE_DIR", tmp_path / "index")
    monkeypatch.setattr(frame_cache, "CACHE_DIR", tmp_path / "arrays")
    text.clear_memory_cache()
    yield
    text.clear_memory_cache()


@pytest.fixture
def handed(monkeypatch):
    """How many frames Rust handed back to Python, per call."""
    seen = []
    real = ffast_xyz.energies

    def spy(*args):
        got = real(*args)
        seen.append(len(got[3]))
        return got
    monkeypatch.setattr(ffast_xyz, "energies", spy)
    return seen


def _energies(path, fast, monkeypatch, threads=1):
    monkeypatch.setattr(text, "fast_parser_usable", lambda: fast)
    monkeypatch.setattr(xa, "worker_count", lambda: threads)
    starts = frame_index.scan(str(path)).starts[:-1]
    try:
        return XyzReader.energies(str(path), starts)
    except Exception as exc:
        return exc


def _same(a, b):
    if isinstance(a, Exception) or isinstance(b, Exception):
        assert type(a) is type(b) and str(a) == str(b), (a, b)
        return
    assert list(a) == list(b)                       # the names, in order
    for name in a:
        x, y = np.asarray(a[name]), np.asarray(b[name])
        assert x.dtype == y.dtype == np.float64, name
        assert np.array_equal(x.view(np.int64), y.view(np.int64)), (name, x, y)   # bit for bit


@pytest.mark.parametrize("name", sorted(HEADER_CASES))
@pytest.mark.parametrize("threads", [1, 4])
def test_the_fast_parser_reads_the_python_energies(name, threads, tmp_path, monkeypatch, handed):
    path = _write(tmp_path, HEADER_CASES[name])
    python = _energies(path, False, monkeypatch)
    assert handed == []
    _same(python, _energies(path, True, monkeypatch, threads))
    key = name.removeprefix("energy ")
    if key in RUST_ALONE:
        assert handed == [0]
    elif key in VALUES:
        assert handed == [3]                        # the Python code read every frame


@pytest.mark.parametrize("name", sorted({**CASES, **FILES}))
def test_the_parity_files_give_the_same_energies(name, tmp_path, monkeypatch, handed):
    path = tmp_path / "case.xyz"
    content = {**CASES, **FILES}[name]
    path.write_bytes(content.encode() if isinstance(content, str) else content)
    try:
        frame_index.scan(str(path))
    except frame_index.FrameIndexError:
        pytest.skip("not a valid frame list")
    _same(_energies(path, False, monkeypatch), _energies(path, True, monkeypatch, 4))


EXAMPLES = sorted(p for p in (Path(__file__).resolve().parents[3] / "examples" / "data").rglob("*")
                  if p.suffix in (".xyz", ".extxyz"))


@pytest.mark.parametrize("path", EXAMPLES, ids=lambda p: p.name)
def test_the_example_files_give_the_same_energies_read_by_rust_alone(path, monkeypatch, handed):
    _same(_energies(path, False, monkeypatch), _energies(path, True, monkeypatch, 4))
    assert handed == [0]


def test_many_frames_on_several_threads_keep_the_order_of_first_appearance(tmp_path, monkeypatch, handed):
    rng = np.random.default_rng(1)
    names = ["energy", "a_energy", "b_energy", "c_energy", "d_energy"]
    headers = []
    for i in range(3000):
        picked = rng.permutation(names)[:int(rng.integers(0, 4))]
        parts = [f"{n}={rng.normal():.17g}" for n in picked]
        if i % 97 == 0:
            parts.append("z_energy=nan")            # Python reads this frame
        headers.append(" ".join(parts + [PROPS]))
    path = _write(tmp_path, headers)
    python = _energies(path, False, monkeypatch)
    for threads in (1, 4, 16):
        _same(python, _energies(path, True, monkeypatch, threads))
    assert handed[-1] == len(range(0, 3000, 97))


def test_frames_asked_for_out_of_order_and_twice(tmp_path, monkeypatch):
    path = _write(tmp_path, ["energy=1", "b_energy=2", "energy=nan a_energy=3", "energy=4"])
    starts = frame_index.scan(str(path)).starts[:-1][[3, 2, 1, 3, 0]]
    got = {}
    for fast in (False, True):
        monkeypatch.setattr(text, "fast_parser_usable", lambda fast=fast: fast)
        got[fast] = XyzReader.energies(str(path), starts)
    _same(got[False], got[True])


def test_with_the_fast_parser_energies_run_without_worker_processes(tmp_path, monkeypatch):
    path = _write(tmp_path, ["energy=1"] * 10)
    monkeypatch.setattr(text, "fast_parser_usable", lambda: True)
    monkeypatch.setattr(text, "ENERGY_PARALLEL_MIN_FRAMES", 0)
    monkeypatch.setattr(text, "ProcessPoolExecutor", None)   # a pool would fail
    found = text.get_energies(XyzReader, str(path), use_cache=False)
    assert np.array_equal(found["energy"], np.ones(10))
