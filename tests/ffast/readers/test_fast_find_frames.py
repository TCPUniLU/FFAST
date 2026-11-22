"""Finding the frames with the fast parser (``ffast_xyz.find_frames``, ADR
0100 step 5) gives ``frame_index.scan``'s starts and atom counts, and a
broken file the same error, word for word.

Each case is scanned with the Python loop only, then with the fast parser,
whole and in tiny chunks (so a chunk's end falls inside frames)."""
import os
from pathlib import Path

import numpy as np
import pytest

ffast_xyz = pytest.importorskip("ffast_xyz", reason="the fast parser (rust/ffast-xyz) is not installed")

from ffast.io import frame_index  # noqa: E402
from ffast.io.frame_index import FrameIndexCancelled, FrameIndexError  # noqa: E402
from ffast.readers import text  # noqa: E402
from tests.ffast.read_once_fixtures import FILES  # noqa: E402
from tests.ffast.readers.test_fast_parser_parity import CASES  # noqa: E402

FRAME = "2\ncomment\nH 0 0 0\nH 0 0 1\n"
BROKEN = {
    "count not a number": FRAME + "two\ncomment\nH 0 0 0\nH 0 0 1\n",
    "count with a sign": FRAME + "+2\ncomment\nH 0 0 0\nH 0 0 1\n",
    "negative count": FRAME + "-1\n" + FRAME,
    "count with an underscore": FRAME + "0_2\ncomment\nH 0 0 0\nH 0 0 1\n",
    "count beyond 18 digits": FRAME + "1" * 19 + "\ncomment\n",
    "count padded with spaces and tabs": FRAME + " \t2 \r\ncomment\nH 0 0 0\nH 0 0 1\n",
    "leading zeros": FRAME + "002\ncomment\nH 0 0 0\nH 0 0 1\n",
    "zero atoms": "0\nempty\n" + FRAME,
    "zero atoms, no comment": FRAME + "0\n",
    "non-ASCII count": FRAME + "２\ncomment\nH 0 0 0\nH 0 0 1\n",
    "ends mid-frame": FRAME + "2\ncomment\nH 0 0 0\n",
    "ends after the count line": FRAME + "2\n",
    "ends after the count, no newline": FRAME + "2",
    "last line without a newline": FRAME + FRAME.rstrip("\n"),
    "blank line between frames": FRAME + "\n" + FRAME,
    "spaces-only line between frames": FRAME + "  \t\n" + FRAME,
    "trailing blank lines": FRAME + FRAME + "\n\n  \n",
    "trailing blank line without newline": FRAME + "   ",
    "blank lines, then text": FRAME + "\n\n\nx\n",
    "blank first line": "\n" + FRAME,
    "empty file": "",
    "only blank lines": "\n \n",
    "CRLF": FRAME.replace("\n", "\r\n") * 3,
    "form feed after the count": FRAME + "2\x0c\ncomment\nH 0 0 0\nH 0 0 1\n",
    "huge frame": "5000\nc\n" + "H 0 0 0\n" * 5000 + FRAME,
}
ALL = {**{f"parity: {k}": v for k, v in CASES.items()},
       **{f"read_once: {k}": v for k, v in FILES.items()}, **BROKEN}
# The fast parser reads these to the end itself, handing nothing to Python.
FAST_ONLY = {"count padded with spaces and tabs", "leading zeros", "zero atoms", "trailing blank lines",
             "trailing blank line without newline", "empty file", "only blank lines", "CRLF",
             "form feed after the count", "huge frame"}

EXAMPLES = sorted(p for p in (Path(__file__).resolve().parents[3] / "examples" / "data").rglob("*")
                  if p.suffix in (".xyz", ".extxyz"))


@pytest.fixture
def states(monkeypatch):
    """The states the fast parser's calls returned."""
    seen = []
    real = ffast_xyz.find_frames
    real_finder = ffast_xyz.FrameFinder

    def spy(path, offset, limit, threads=1):
        got = real(path, offset, limit, threads)
        seen.append(got[3])
        return got

    class Spy:
        def __init__(self, *args):
            self.finder = real_finder(*args)

        def find(self, offset, limit):
            got = self.finder.find(offset, limit)
            seen.append(got[3])
            return got
    monkeypatch.setattr(ffast_xyz, "find_frames", spy)
    monkeypatch.setattr(ffast_xyz, "FrameFinder", Spy)
    return seen


def _scan(path, fast, monkeypatch, **kw):
    monkeypatch.setattr(text, "fast_parser_usable", lambda: fast)
    try:
        index = frame_index.scan(str(path), **kw)
        return index.starts, index.counts
    except FrameIndexError as exc:
        return exc


def _same(a, b):
    if isinstance(a, Exception) or isinstance(b, Exception):
        assert type(a) is type(b) and str(a) == str(b), (a, b)
        return
    for x, y in zip(a, b):
        assert x.dtype == y.dtype == np.int64
        assert np.array_equal(x, y)


def _write(tmp_path, content):
    path = tmp_path / "case.xyz"
    path.write_bytes(content.encode() if isinstance(content, str) else content)
    return path


@pytest.mark.parametrize("name", sorted(ALL))
@pytest.mark.parametrize("chunk", [frame_index.FIND_BYTES, 1, 7])
def test_the_fast_parser_finds_the_python_scans_frames(name, chunk, tmp_path, monkeypatch, states):
    monkeypatch.setattr(frame_index, "FIND_BYTES", chunk)
    path = _write(tmp_path, ALL[name])
    python = _scan(path, False, monkeypatch)
    assert states == []
    _same(python, _scan(path, True, monkeypatch))
    assert states and all(s == ffast_xyz.FIND_MORE for s in states[:-1])
    if name in FAST_ONLY:
        assert states[-1] == ffast_xyz.FIND_END


@pytest.mark.parametrize("path", EXAMPLES, ids=lambda p: p.name)
def test_the_example_files_are_found_by_the_fast_parser_alone(path, monkeypatch, states):
    _same(_scan(path, False, monkeypatch), _scan(path, True, monkeypatch))
    assert states[-1] == ffast_xyz.FIND_END


@pytest.mark.parametrize("threads", [1, 4])
def test_line_ends_found_on_several_threads_give_the_same_frames(threads, tmp_path, monkeypatch, states):
    # Over 4 MB pieces, frames of 1–40 atoms with lines of uneven length cross
    # every piece's edge; the chunk ends inside a frame too.
    rng = np.random.default_rng(0)
    parts = []
    for i in range(60_000):
        n = int(rng.integers(1, 40))
        parts.append(f"{n}\nframe {i}\n" + "".join(f"H {a} {'0' * int(rng.integers(0, 9))} 1\n"
                                                    for a in range(n)))
    path = _write(tmp_path, "".join(parts) + "\n")
    monkeypatch.setattr("ffast.io.extxyz_arrays.worker_count", lambda: threads)
    monkeypatch.setattr(frame_index, "FIND_BYTES", 9 * 2**20 + 3)
    _same(_scan(path, False, monkeypatch), _scan(path, True, monkeypatch))
    assert len(states) > 1 and states[-1] == ffast_xyz.FIND_END


def test_progress_moves_per_chunk_and_a_cancel_stops_the_scan(tmp_path, monkeypatch):
    path = _write(tmp_path, FRAME * 10)
    monkeypatch.setattr(frame_index, "FIND_BYTES", 3 * len(FRAME))
    moved = []
    _scan(path, True, monkeypatch, progress=moved.append)
    assert moved == [3, 6, 9]
    with pytest.raises(FrameIndexCancelled):
        _scan(path, True, monkeypatch, progress=moved.append, should_stop=lambda: True)


def test_the_phase_timings_are_off_unless_asked_for(tmp_path, monkeypatch):
    assert not os.environ.get("FFAST_XYZ_TIMING")
    _scan(_write(tmp_path, FRAME * 3), True, monkeypatch)
    assert ffast_xyz.timings() == {}


def test_a_package_without_find_frames_leaves_the_scan_to_python(tmp_path, monkeypatch):
    monkeypatch.delattr(ffast_xyz, "find_frames")
    monkeypatch.setattr(text, "fast_parser_usable", lambda: True)
    assert frame_index._fast_finder() is None
    starts, counts = _scan(_write(tmp_path, FRAME * 2), True, monkeypatch)
    assert counts.tolist() == [2, 2] and starts.tolist() == [0, len(FRAME), 2 * len(FRAME)]


# ── the finder that reads ahead (FrameFinder) ──────────────────────────────

def _uneven_frames(count, seed=0):
    rng = np.random.default_rng(seed)
    return "".join(f"{n}\nframe {i}\n" + "".join(f"H {a} {'0' * int(rng.integers(0, 9))} 1\n" for a in range(n))
                   for i, n in enumerate(rng.integers(1, 40, count)))


def _walk(find, size, limits):
    """Calls ``find(offset, limit)`` from 0 until the end, each with the next
    of ``limits`` (cycled); every call's answer."""
    got, pos, k = [], 0, 0
    while True:
        s, c, pos, state = find(pos, limits[k % len(limits)])
        got.append((s.tolist(), c.tolist(), pos, state))
        k += 1
        if state != ffast_xyz.FIND_MORE or pos >= size:
            return got


@pytest.mark.parametrize("ahead", [0, 1, 3])
@pytest.mark.parametrize("limits", [[997], [5000, 300, 20_000], [9 * 2**20 + 3]], ids=["small", "changing", "9MB"])
def test_the_finder_reading_ahead_answers_each_call_as_find_frames_does(ahead, limits, tmp_path):
    # Same windows, not only the same frames: a resumed scan and the cache's
    # per-window fingerprints (ADR 0107) rely on them.
    path = _write(tmp_path, _uneven_frames(60_000))
    size = path.stat().st_size
    plain = _walk(lambda o, n: ffast_xyz.find_frames(str(path), o, n, 4), size, limits)
    assert _walk(ffast_xyz.FrameFinder(str(path), 4, ahead).find, size, limits) == plain
    assert len(plain) > 1 and plain[-1][3] == ffast_xyz.FIND_END


def test_a_call_that_does_not_follow_the_last_one_reads_its_own_window(tmp_path):
    path = _write(tmp_path, FRAME * 1000)
    finder = ffast_xyz.FrameFinder(str(path), 2, 2)
    finder.find(0, 10 * len(FRAME))
    for offset in (500 * len(FRAME), 3 * len(FRAME), 0, 999 * len(FRAME)):     # back, forth, repeated
        got = finder.find(offset, 7 * len(FRAME))
        want = ffast_xyz.find_frames(str(path), offset, 7 * len(FRAME), 2)
        assert [x.tolist() if hasattr(x, "tolist") else x for x in got] == \
               [x.tolist() if hasattr(x, "tolist") else x for x in want]


def test_a_frame_longer_than_the_carry_room_crosses_windows(tmp_path):
    # Window 1 ends 5 MB into a 6 MB frame: more than the 4 MB kept before
    # each window for the frame carried over, so the two are joined by copying.
    small = "3\nsmall\n" + "H 0 0 0\n" * 3
    content = (small * (3 * 2**20 // len(small)) + f"{6 * 2**20 // 8}\nbig\n" + "H 0 0 0\n" * (6 * 2**20 // 8)
               + small * 1000)
    path = _write(tmp_path, content)
    size = path.stat().st_size
    for limits in ([8 * 2**20], [2**20]):
        plain = _walk(lambda o, n: ffast_xyz.find_frames(str(path), o, n, 4), size, limits)
        assert _walk(ffast_xyz.FrameFinder(str(path), 4, 2).find, size, limits) == plain


def test_a_scan_reads_ahead_only_when_the_budget_has_room_for_it(tmp_path, monkeypatch):
    from ffast.io import budget as budget_module
    made = []
    real = ffast_xyz.FrameFinder
    monkeypatch.setattr(ffast_xyz, "FrameFinder", lambda *a: made.append(a) or real(*a))
    path = _write(tmp_path, FRAME * 10)
    window = 3 * len(FRAME)
    monkeypatch.setattr(frame_index, "FIND_BYTES", window)
    owner = budget_module.budget()
    monkeypatch.setattr(owner, "limit", owner.used + window * frame_index.READ_AHEAD_ROOM)
    _same(_scan(path, False, monkeypatch), _scan(path, True, monkeypatch))
    assert made == [(str(path), text.fast_threads(), frame_index.READ_AHEAD)]
    monkeypatch.setattr(owner, "limit", owner.used + window * frame_index.READ_AHEAD_ROOM - 1)
    _same(_scan(path, False, monkeypatch), _scan(path, True, monkeypatch))
    assert len(made) == 1


def test_a_package_without_the_read_ahead_finder_scans_with_find_frames(tmp_path, monkeypatch, states):
    monkeypatch.delattr(ffast_xyz, "FrameFinder")
    path = _write(tmp_path, FRAME * 10)
    monkeypatch.setattr(frame_index, "FIND_BYTES", 3 * len(FRAME))
    _same(_scan(path, False, monkeypatch), _scan(path, True, monkeypatch))
    assert len(states) > 1
