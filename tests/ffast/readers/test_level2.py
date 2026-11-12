"""Level 2, the fast text contract (ADR 0095, decision 3): FFAST supplies the
frame list and its cache, stride without parsing skipped frames, workers,
progress, the memory check and the read-once cache."""
import numpy as np
import pytest

from ffast.io import frame_cache, frame_index
from ffast.io.extxyz_arrays import NotEnoughMemory, NotFast
from ffast.readers import ReaderError, check_reader, chunks, text
from tests.ffast.readers.toy_reader import ToyTextReader, make_frames, write_toy


@pytest.fixture(autouse=True)
def _fresh(tmp_path, monkeypatch):
    monkeypatch.setattr(frame_index, "CACHE_DIR", tmp_path / "index")
    monkeypatch.setattr(frame_cache, "CACHE_DIR", tmp_path / "arrays")
    text.clear_memory_cache()
    ToyTextReader.found, ToyTextReader.parsed = [], []
    yield
    text.clear_memory_cache()


def _file(tmp_path, n=12, **kw):
    return write_toy(tmp_path / "a.toyt", make_frames(n, **kw))


def test_the_reader_follows_the_level_2_rules(tmp_path):
    check_reader(ToyTextReader, _file(tmp_path, 7, variable=True))


def test_every_frame_is_read_and_joined_in_order(tmp_path):
    path = _file(tmp_path, 9, variable=True)
    loaded = chunks.read_arrays(ToyTextReader, path)
    assert loaded.frames == list(range(9)) and loaded.n_file_frames == 9
    assert loaded.arrays["n_atoms"].tolist() == [3, 4, 5, 3, 4, 5, 3, 4, 5]
    assert loaded.arrays["energy:dft"].tolist() == [i * 10.0 for i in range(9)]
    assert loaded.arrays["positions"].shape == (36, 3)
    assert loaded.contents["energies"] == ["dft"]


def test_a_second_load_does_not_call_find_frames_again(tmp_path):
    path = _file(tmp_path)
    chunks.read_arrays(ToyTextReader, path, use_cache=False)
    chunks.read_arrays(ToyTextReader, path, use_cache=False)
    assert len(ToyTextReader.found) == 1                       # the memory cache
    text.clear_memory_cache()
    chunks.read_arrays(ToyTextReader, path, use_cache=False)
    assert len(ToyTextReader.found) == 1                       # the saved frame list on disk


def test_the_frame_list_is_kept_per_reader_and_file_and_follows_the_file(tmp_path):
    path = _file(tmp_path, 6)
    chunks.read_arrays(ToyTextReader, path, use_cache=False)
    text.clear_memory_cache()
    write_toy(tmp_path / "a.toyt", make_frames(8))               # the file changed
    assert len(chunks.read_arrays(ToyTextReader, path, use_cache=False).frames) == 8
    assert len(ToyTextReader.found) == 2


def test_a_stride_parses_only_the_frames_it_keeps(tmp_path):
    path = _file(tmp_path, 12)
    loaded = chunks.read_arrays(ToyTextReader, path, stride=3, use_cache=False)
    assert loaded.frames == [0, 3, 6, 9]
    assert loaded.arrays["energy:dft"].tolist() == [0.0, 30.0, 60.0, 90.0]
    assert sum(n for _, n in ToyTextReader.parsed) == 4        # never the other 8


def test_select_names_the_frames_and_may_refuse(tmp_path):
    path = _file(tmp_path, 10)
    loaded = chunks.read_arrays(ToyTextReader, path, select=lambda n: range(1, n, 4), use_cache=False)
    assert loaded.frames == [1, 5, 9]

    def refuse(n):
        raise ReaderError(f"the file has {n} frames")
    with pytest.raises(ReaderError, match="the file has 10 frames"):
        chunks.read_arrays(ToyTextReader, path, select=refuse, use_cache=False)


def test_progress_reaches_the_total(tmp_path):
    path = _file(tmp_path, 12)
    seen = []
    chunks.read_arrays(ToyTextReader, path, stride=2, use_cache=False, batch=2,
                       progress=lambda done, total: seen.append((done, total)))
    assert seen[-1] == (6, 6) and [d for d, _ in seen] == sorted({d for d, _ in seen})


def test_workers_give_the_same_arrays(tmp_path):
    path = _file(tmp_path, 40, variable=True)
    serial = chunks.read_arrays(ToyTextReader, path, use_cache=False, workers=1, batch=7)
    parallel = chunks.read_arrays(ToyTextReader, path, use_cache=False, workers=2, batch=7)
    assert serial.arrays.keys() == parallel.arrays.keys()
    for key in serial.arrays:
        assert np.array_equal(serial.arrays[key], parallel.arrays[key]), key


def test_the_memory_check_refuses_before_reading_on_and_stops_midway(tmp_path, monkeypatch):
    path = _file(tmp_path, 60)
    monkeypatch.setattr(text, "available_memory", lambda: 2000)
    with pytest.raises(NotEnoughMemory, match=r"a.toyt needs about .* of memory to load "
                                              r"\(\d+ atoms in 60 frames\), but .* is free"):
        chunks.read_arrays(ToyTextReader, path, use_cache=False)
    free = iter([10**12] + [text.MEMORY_FLOOR // 2] * 100)
    monkeypatch.setattr(text, "available_memory", lambda: next(free))
    with pytest.raises(NotEnoughMemory, match=r"Stopped reading a.toyt after \d+ of 60 frames"):
        chunks.read_arrays(ToyTextReader, path, use_cache=False, workers=1, batch=5)


def test_a_key_some_batches_lack_is_nan_there(tmp_path):
    class Gappy(ToyTextReader):
        name = "gappy"

        @staticmethod
        def parse(data, first_frame):
            out = ToyTextReader.parse(data, first_frame)
            if first_frame >= 4:
                del out["energy:dft"]
            return out

    path = _file(tmp_path, 8)
    out = chunks.read_arrays(Gappy, path, use_cache=False, workers=1, batch=4).arrays["energy:dft"]
    assert out[:4].tolist() == [0.0, 10.0, 20.0, 30.0] and np.isnan(out[4:]).all()


def test_notfast_passes_through_for_the_caller_to_fall_back(tmp_path):
    class Strict(ToyTextReader):
        name = "strict2"

        @staticmethod
        def parse(data, first_frame):
            raise NotFast("columns change")

    with pytest.raises(NotFast, match="columns change"):
        chunks.read_arrays(Strict, _file(tmp_path), use_cache=False)


def test_a_parse_error_is_wrapped_like_a_level_1_error(tmp_path):
    class Careless(ToyTextReader):
        name = "careless2"

        @staticmethod
        def parse(data, first_frame):
            return {}["x"]

    with pytest.raises(ReaderError, match="careless2 reader could not read a.toyt: 'x'"):
        chunks.read_arrays(Careless, _file(tmp_path), use_cache=False)


def test_the_read_once_cache_opens_the_kept_arrays_next_time(tmp_path):
    path = _file(tmp_path, 12)
    first = chunks.read_arrays(ToyTextReader, path, stride=3)
    n_parsed = len(ToyTextReader.parsed)
    second = chunks.read_arrays(ToyTextReader, path, stride=3)
    assert len(ToyTextReader.parsed) == n_parsed               # nothing parsed again
    assert second.frames == first.frames and second.n_file_frames == 12
    for key in first.arrays:
        assert np.array_equal(first.arrays[key], second.arrays[key]), key
    assert not second.arrays["positions"].flags.writeable       # opened read-only
    chunks.read_arrays(ToyTextReader, path, stride=2)           # another stride: parsed afresh
    assert len(ToyTextReader.parsed) > n_parsed


def test_the_cache_follows_the_file_and_the_readers_version(tmp_path):
    path = _file(tmp_path, 6)
    chunks.read_arrays(ToyTextReader, path)
    write_toy(tmp_path / "a.toyt", make_frames(6, shift=1.0))
    assert chunks.read_arrays(ToyTextReader, path).arrays["positions"][0, 0] == 1.0

    class Newer(ToyTextReader):
        cache_version = 2
    n = len(ToyTextReader.parsed)
    chunks.read_arrays(Newer, path)
    assert len(ToyTextReader.parsed) > n
