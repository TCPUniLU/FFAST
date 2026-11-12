"""FFAST reads a reader's frames in increasing chunks, applies the stride,
reports progress, checks memory, and wraps errors."""
import asyncio
import logging

import numpy as np
import pytest

from ffast.io.extxyz_arrays import NotEnoughMemory
from ffast.protocol import control
from ffast.protocol.rpc import unpack
from ffast.readers import ReaderError, chunks, warn
from ffast.session.warning_relay import WarningRelay
from tests.ffast.readers.toy_reader import ToyReader, make_frames, write_toy


def test_every_frame_is_read_and_joined(toy, tmp_path):
    path = write_toy(tmp_path / "a.toy", make_frames(5, variable=True))
    loaded = chunks.read_arrays(ToyReader, path)
    arrays = loaded.arrays
    counts = [3, 4, 5, 3, 4]
    assert arrays["n_atoms"].tolist() == counts
    assert arrays["positions"].shape == (sum(counts), 3)
    assert arrays["energy:dft"].tolist() == [0.0, 10.0, 20.0, 30.0, 40.0]
    assert loaded.frames == [0, 1, 2, 3, 4]
    assert loaded.n_file_frames == 5


def test_chunks_come_in_increasing_order_and_join_to_what_one_read_gives(toy, tmp_path):
    path = write_toy(tmp_path / "a.toy", make_frames(20, variable=True))
    whole = ToyReader(path).read(list(range(20)))
    ToyReader.asked = []
    loaded = chunks.read_arrays(ToyReader, path, chunk_bytes=1, first_frames=4)
    asked = ToyReader.asked
    assert len(asked) > 2
    assert [f for chunk in asked for f in chunk] == list(range(20))
    assert asked[0] == [0, 1, 2, 3]
    assert loaded.arrays.keys() == whole.keys()
    for key in whole:
        assert np.array_equal(loaded.arrays[key], whole[key]), key


def test_a_stride_of_3_reads_frames_0_3_6_and_never_asks_for_the_others(toy, tmp_path):
    path = write_toy(tmp_path / "a.toy", make_frames(10))
    loaded = chunks.read_arrays(ToyReader, path, stride=3)
    assert loaded.frames == [0, 3, 6, 9]
    assert loaded.n_file_frames == 10
    assert sorted({f for chunk in ToyReader.asked for f in chunk}) == [0, 3, 6, 9]
    assert loaded.arrays["energy:dft"].tolist() == [0.0, 30.0, 60.0, 90.0]


@pytest.mark.parametrize("stride", [None, 0, 1, -1])
def test_no_stride_or_a_stride_of_one_keeps_every_frame(toy, tmp_path, stride):
    path = write_toy(tmp_path / "a.toy", make_frames(4))
    assert chunks.read_arrays(ToyReader, path, stride=stride).frames == [0, 1, 2, 3]


def test_progress_follows_each_chunk_and_reaches_the_total(toy, tmp_path):
    path = write_toy(tmp_path / "a.toy", make_frames(12))
    seen = []
    chunks.read_arrays(ToyReader, path, stride=2, chunk_bytes=1, first_frames=2,
                       progress=lambda done, total: seen.append((done, total)))
    assert seen[-1] == (6, 6)
    assert [d for d, _ in seen] == sorted({d for d, _ in seen})
    assert {t for _, t in seen} == {6}


def test_a_reader_error_keeps_its_text_as_it_is(toy, tmp_path):
    class Strict(ToyReader):
        name = "strict"

        def read(self, frames):
            raise ReaderError("Frame 12 has no forces")

    path = write_toy(tmp_path / "a.toy", make_frames(2))
    with pytest.raises(ReaderError) as caught:
        chunks.read_arrays(Strict, path)
    assert str(caught.value) == "Frame 12 has no forces"


def test_any_other_exception_is_wrapped_with_the_reader_and_the_file_and_logged_with_its_traceback(
        toy, tmp_path, caplog):
    class Careless(ToyReader):
        name = "careless"

        def read(self, frames):
            return {}["forces"]

    path = write_toy(tmp_path / "run.toy", make_frames(2))
    with caplog.at_level(logging.DEBUG, logger="FFAST"):
        with pytest.raises(ReaderError) as caught:
            chunks.read_arrays(Careless, path)
    assert str(caught.value) == "careless reader could not read run.toy: 'forces'"
    traced = [r for r in caplog.records if r.exc_info]
    assert traced and all(getattr(r, "popup", True) is False for r in traced)


def test_an_error_in_the_constructor_or_n_frames_is_wrapped_too(toy, tmp_path):
    class NoFile(ToyReader):
        name = "nofile"

        def n_frames(self):
            raise OSError("disk gone")

    path = write_toy(tmp_path / "run.toy", make_frames(2))
    with pytest.raises(ReaderError, match="nofile reader could not read run.toy: disk gone"):
        chunks.read_arrays(NoFile, path)
    with pytest.raises(ReaderError, match="toy reader could not read nothing.toy"):
        chunks.read_arrays(ToyReader, str(tmp_path / "nothing.toy"))


def test_warn_reaches_the_pop_up_and_bell_path_and_the_load_goes_on(toy, tmp_path):
    class Warns(ToyReader):
        name = "warns"

        def read(self, frames):
            warn("3 frames have no cell")
            return super().read(frames)

    class _Loop:
        def call_soon_threadsafe(self, fn, *args):
            fn(*args)

    sent = []
    relay = WarningRelay(_Loop(), sent.append)
    log = logging.getLogger("FFAST")
    log.addHandler(relay)
    try:
        path = write_toy(tmp_path / "a.toy", make_frames(3))
        loaded = chunks.read_arrays(Warns, path)
    finally:
        log.removeHandler(relay)
    assert len(loaded.frames) == 3
    [(event, _args, kwargs)] = [unpack(d) for d in sent]
    assert event == control.SERVER_WARNING
    assert kwargs == {"message": "3 frames have no cell", "level": "warning"}


def test_the_memory_check_refuses_when_the_first_chunk_predicts_more_than_is_free(
        toy, tmp_path, monkeypatch):
    path = write_toy(tmp_path / "big.toy", make_frames(50))
    monkeypatch.setattr(chunks, "available_memory", lambda: 2000)
    with pytest.raises(NotEnoughMemory, match=r"big.toy needs about .* of memory to load "
                                              r"\(\d+ atoms in 50 frames\), but .* is free"):
        chunks.read_arrays(ToyReader, path, first_frames=5)
    assert len(ToyReader.asked) == 1            # it stopped after the first chunk


def test_the_memory_check_stops_a_read_when_memory_runs_out_midway(toy, tmp_path, monkeypatch):
    path = write_toy(tmp_path / "a.toy", make_frames(30))
    free = iter([10**12] + [chunks.MEMORY_FLOOR // 2] * 100)
    monkeypatch.setattr(chunks, "available_memory", lambda: next(free))
    with pytest.raises(NotEnoughMemory, match=r"Stopped reading a.toy after \d+ of 30 frames"):
        chunks.read_arrays(ToyReader, path, chunk_bytes=1, first_frames=2)


def test_a_reader_that_returns_keys_that_change_between_chunks_is_named(toy, tmp_path):
    class Wobbly(ToyReader):
        name = "wobbly"

        def read(self, frames):
            out = super().read(frames)
            if frames[0] > 0:
                del out["energy:dft"]
            return out

    path = write_toy(tmp_path / "a.toy", make_frames(6))
    with pytest.raises(ReaderError, match="wobbly reader could not read a.toy: .*energy:dft"):
        chunks.read_arrays(Wobbly, path, chunk_bytes=1, first_frames=2)
