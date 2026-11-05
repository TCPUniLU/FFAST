"""The incremental reader for FFAST's LAMMPS dumps (ADR 0087)."""

from __future__ import annotations

import os

import numpy as np
import pytest

from ffast.live.dump_reader import DumpError, DumpReader, DumpReplaced
from tests.ffast.live.dump_helpers import ASPIRIN_ELEMENTS, dump_text, random_frames


def _write(path, data: bytes, mode="wb"):
    with open(path, mode) as fh:
        fh.write(data)


def _same(a, b):
    assert a.step == b.step and a.time == b.time and a.units == b.units
    assert np.array_equal(a.ids, b.ids) and a.elements == b.elements
    assert np.array_equal(a.positions, b.positions)


@pytest.fixture
def three(tmp_path):
    frames = random_frames(3)
    data = dump_text(frames).encode()
    return tmp_path / "frames.lammpstrj", frames, data


def test_a_whole_dump_reads_every_frame_with_steps_time_ids_and_exact_positions(three):
    path, frames, data = three
    _write(path, data)
    got = DumpReader(path).poll()
    assert [f.step for f in got] == [0, 100, 200]
    assert [f.time for f in got] == [0.0, 0.05, 0.1]
    assert [f.index for f in got] == [0, 1, 2]
    for f, p in zip(got, frames):
        assert f.elements == ASPIRIN_ELEMENTS
        assert list(f.ids) == list(range(1, 22))
        assert list(f.types[:3]) == [1, 1, 1]
        assert f.positions.dtype == np.float64
        # %.17g round-trips: the bits are the same as the array written
        assert np.array_equal(f.positions, p)


def test_units_given_only_in_frame_one_are_carried_forward(three):
    path, _, data = three
    assert data.count(b"ITEM: UNITS") == 1
    _write(path, data)
    assert [f.units for f in DumpReader(path).poll()] == ["metal"] * 3


def test_a_dump_cut_at_every_byte_returns_only_complete_frames_then_the_rest(three):
    path, _, data = three
    _write(path, data)
    expected = DumpReader(path).poll()
    ends = [f.offset + f.length for f in expected]
    for cut in range(len(data) + 1):
        _write(path, data[:cut])
        reader = DumpReader(path)
        first = reader.poll()
        assert len(first) == sum(1 for e in ends if e <= cut), cut
        _write(path, data[cut:], mode="ab")
        rest = reader.poll()
        both = first + rest
        assert len(both) == 3, cut
        for a, b in zip(both, expected):
            _same(a, b)


def test_a_file_growing_in_random_chunks_reads_the_same_as_one_read(three):
    path, _, data = three
    _write(path, data)
    expected = DumpReader(path).poll()
    rng = np.random.default_rng(1)
    _write(path, b"")
    reader = DumpReader(path, max_bytes=97)
    got, pos = [], 0
    while pos < len(data) or len(got) < 3:
        step = int(rng.integers(1, 400))
        if pos < len(data):
            _write(path, data[pos:pos + step], mode="ab")
            pos += step
        got += reader.poll()
    for a, b in zip(got, expected):
        _same(a, b)


def test_a_missing_file_is_not_yet(tmp_path):
    reader = DumpReader(tmp_path / "frames.lammpstrj")
    assert reader.poll() == []
    assert len(reader) == 0


def test_a_path_with_a_space_is_refused(tmp_path):
    with pytest.raises(ValueError, match="space"):
        DumpReader(tmp_path / "with space" / "frames.lammpstrj")


def test_a_replaced_file_is_detected(three, tmp_path):
    path, _, data = three
    _write(path, data)
    reader = DumpReader(path)
    reader.poll()
    # a new file at the same path (new inode)
    other = tmp_path / "other"
    _write(other, data)
    os.replace(other, path)
    with pytest.raises(DumpReplaced):
        reader.poll()


def test_a_file_that_got_shorter_is_replaced(three):
    path, _, data = three
    _write(path, data)
    reader = DumpReader(path)
    reader.poll()
    with open(path, "r+b") as fh:
        fh.truncate(len(data) // 2)
    with pytest.raises(DumpReplaced):
        reader.poll()


def test_a_file_rewritten_in_place_with_a_new_start_is_replaced(three, tmp_path):
    path, _, data = three
    _write(path, data)
    reader = DumpReader(path)
    reader.poll()
    changed = data.replace(b"metal", b"real ", 1)
    with open(path, "r+b") as fh:
        fh.write(changed[:64])
    with pytest.raises(DumpReplaced):
        reader.poll()


def test_another_column_layout_is_refused_naming_the_line(tmp_path):
    path = tmp_path / "frames.lammpstrj"
    text = dump_text(random_frames(1)).replace("id type element x y z", "id type x y z")
    _write(path, text.encode())
    with pytest.raises(DumpError, match="id type x y z"):
        DumpReader(path).poll()


def test_an_unknown_header_line_is_refused(tmp_path):
    path = tmp_path / "frames.lammpstrj"
    _write(path, b"hello\n")
    with pytest.raises(DumpError, match="hello"):
        DumpReader(path).poll()


def test_atoms_added_or_removed_is_refused(tmp_path):
    path = tmp_path / "frames.lammpstrj"
    frames = random_frames(2)
    text = dump_text(frames[:1]) + dump_text([frames[1][:20]], ASPIRIN_ELEMENTS[:20]).replace(
        "ITEM: UNITS\nmetal\n", "")
    _write(path, text.encode())
    with pytest.raises(DumpError, match="atoms"):
        DumpReader(path).poll()


def test_a_first_frame_without_units_is_refused(tmp_path):
    path = tmp_path / "frames.lammpstrj"
    text = dump_text(random_frames(1)).replace("ITEM: UNITS\nmetal\n", "")
    _write(path, text.encode())
    with pytest.raises(DumpError, match="UNITS"):
        DumpReader(path).poll()


def test_finish_reports_the_bytes_of_an_incomplete_tail(three):
    path, _, data = three
    _write(path, data[:-10])
    reader = DumpReader(path)
    frames, tail = reader.finish()
    assert len(frames) == 2
    first_two_end = frames[-1].offset + frames[-1].length
    assert tail == len(data) - 10 - first_two_end


def test_finish_reads_past_the_per_poll_limit(three):
    path, _, data = three
    _write(path, data)
    frames, tail = DumpReader(path, max_bytes=50).finish()
    assert len(frames) == 3 and tail == 0


def test_frame_at_returns_the_frame_polled_earlier(three):
    path, _, data = three
    _write(path, data)
    reader = DumpReader(path)
    polled = reader.poll()
    assert len(reader) == 3
    for k in (2, 0, 1):
        _same(reader.frame_at(k), polled[k])
    assert reader.frame_at(1).units == "metal"
    with pytest.raises(IndexError):
        reader.frame_at(3)
