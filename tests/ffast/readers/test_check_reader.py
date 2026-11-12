"""``check_reader(cls, sample_file)`` tells an author whether a reader follows
the rules, with a plain message naming the rule broken."""
import numpy as np
import pytest

from ffast.readers import check_reader
from tests.ffast.readers.toy_reader import ToyReader, make_frames, write_toy


@pytest.fixture
def sample(tmp_path):
    return write_toy(tmp_path / "s.toy", make_frames(7, variable=True, cell=True))


def _broken(**overrides):
    return type("Broken", (ToyReader,), overrides)


def _bad(sample, cls, message):
    with pytest.raises(AssertionError, match=message):
        check_reader(cls, sample)


def test_the_toy_reader_passes(sample):
    check_reader(ToyReader, sample)


def test_a_missing_name_or_extensions_is_named(sample):
    _bad(sample, _broken(name=""), "name")
    _bad(sample, _broken(extensions="*.toy"), "extensions must be a list")


def test_a_missing_method_is_named(sample):
    class NoRead:
        name = "noread"
        extensions = ["*.toy"]

        def __init__(self, path): ...
        def n_frames(self): return 1
        def contents(self): return {}

    _bad(sample, NoRead, "read")


def test_a_missing_key_is_named(sample):
    def read(self, frames):
        out = ToyReader.read(self, frames)
        del out["n_atoms"]
        return out
    _bad(sample, _broken(read=read), "n_atoms")


def test_a_key_the_contents_do_not_list_is_named(sample):
    def read(self, frames):
        out = ToyReader.read(self, frames)
        out["energy:mace"] = np.zeros(len(frames))
        return out
    _bad(sample, _broken(read=read), "energy:mace.*contents")


def test_a_key_the_contents_promise_but_read_leaves_out_is_named(sample):
    def contents(self):
        return {**ToyReader.contents(self), "energies": ["dft", "mace"]}
    _bad(sample, _broken(contents=contents), "energy:mace")


def test_a_wrong_shape_is_named(sample):
    def read(self, frames):
        out = ToyReader.read(self, frames)
        out["positions"] = out["positions"][:, :2]
        return out
    _bad(sample, _broken(read=read), "positions.*shape")


def test_a_wrong_per_frame_length_is_named(sample):
    def read(self, frames):
        out = ToyReader.read(self, frames)
        out["energy:dft"] = out["energy:dft"][:-1]
        return out
    _bad(sample, _broken(read=read), "energy:dft.*one value per frame")


def test_a_wrong_dtype_is_named(sample):
    def read(self, frames):
        out = ToyReader.read(self, frames)
        out["numbers"] = out["numbers"].astype(float)
        return out
    _bad(sample, _broken(read=read), "numbers.*integer")


def test_frames_out_of_order_are_named(sample):
    def read(self, frames):
        return ToyReader.read(self, sorted(frames, reverse=True))
    _bad(sample, _broken(read=read), "in the order asked \\(increasing order\\)")


def test_a_reader_that_answers_chunks_differently_from_one_read_is_named(sample):
    def read(self, frames):
        out = ToyReader.read(self, frames)
        if len(frames) == 1:
            out["positions"] = out["positions"] + 1
        return out
    _bad(sample, _broken(read=read), "chunks.*at once")


def test_n_frames_larger_than_the_file_is_named(sample):
    _bad(sample, _broken(n_frames=lambda self: 99), "n_frames")
