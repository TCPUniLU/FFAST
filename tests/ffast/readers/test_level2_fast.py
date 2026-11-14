"""Level 2 with a fast parser (ADR 0100): FFAST makes the arrays, calls the
reader's ``parse_into`` once per group of frames, joins the header values as
``parse``'s batches join, and hands the file to ``parse`` on ``NotFast``.
The fast parser here is a toy in Python; no compiled package is needed."""
import logging
import types

import numpy as np
import pytest

from ffast.io import frame_cache, frame_index
from ffast.io.extxyz_arrays import NotEnoughMemory, NotFast
from ffast.readers import chunks, text
from tests.ffast.readers.toy_fast_reader import ToyFastReader
from tests.ffast.readers.toy_reader import ToyTextReader, make_frames, write_toy


@pytest.fixture(autouse=True)
def _fresh(tmp_path, monkeypatch):
    monkeypatch.setattr(frame_index, "CACHE_DIR", tmp_path / "index")
    monkeypatch.setattr(frame_cache, "CACHE_DIR", tmp_path / "arrays")
    monkeypatch.setattr(text, "fast_parser_usable", lambda: True)
    text.clear_memory_cache()
    ToyTextReader.parsed, ToyFastReader.calls = [], []
    yield
    text.clear_memory_cache()


def _file(tmp_path, frames):
    return write_toy(tmp_path / "a.toyq", frames)


def _frames(n, change=None):
    """``n`` toy frames of 3–5 atoms; ``change(i, frame)`` edits frame i."""
    frames = make_frames(n, variable=True)
    for i, f in enumerate(frames):
        f["energy"] = i * 10                      # whole numbers
        f["charge"] = i % 2
        if change:
            change(i, f)
    return frames


def _read(path, fast=True, **kw):
    kw.setdefault("use_cache", False)
    if not fast:
        text.fast_parser_usable = lambda: False   # undone by monkeypatch at teardown
    try:
        return chunks.read_arrays(ToyFastReader, path, **kw)
    finally:
        text.fast_parser_usable = lambda: True


def _same(a, b):
    assert sorted(a) == sorted(b)
    for key in a:
        x, y = np.asarray(a[key]), np.asarray(b[key])
        assert x.dtype == y.dtype, (key, x.dtype, y.dtype)
        assert x.shape == y.shape, key
        assert np.array_equal(x, y, equal_nan=x.dtype.kind == "f"), key


def _both(path, groups=(1, 3, None), batches=(2, 5), **kw):
    """The fast parser over several group sizes equals the Python parser over
    several batch sizes."""
    python = _read(path, fast=False, batch=batches[0], **kw).arrays
    for b in batches[1:]:
        _same(python, _read(path, fast=False, batch=b, **kw).arrays)
    for g in groups:
        ToyFastReader.calls.clear()
        _same(python, _read(path, batch=g, **kw).arrays)
        assert ToyFastReader.calls
    return python


def test_the_fast_parser_reads_the_file_and_parse_is_not_called(tmp_path):
    path = _file(tmp_path, _frames(9))
    loaded = _read(path, workers=3)
    assert ToyTextReader.parsed == []
    assert loaded.arrays["n_atoms"].tolist() == [3, 4, 5] * 3
    assert loaded.arrays["energy:dft"].dtype == np.int64
    assert loaded.arrays["frame:charge"].dtype == np.float64
    assert {threads for *_, threads in ToyFastReader.calls} == {3}


def test_it_gives_the_python_parsers_arrays_for_any_group_size(tmp_path):
    _both(_file(tmp_path, _frames(11)))


def test_a_whole_number_key_turns_float_when_one_frame_is_not_whole_or_lacks_it(tmp_path):
    def one_float(i, f):
        if i == 7:
            f["energy"] = 7.5
    out = _both(_file(tmp_path, _frames(10, one_float)))
    assert out["energy:dft"].dtype == np.float64 and out["energy:dft"][7] == 7.5

    def one_missing(i, f):
        if i == 8:
            del f["energy"]
    (tmp_path / "b").mkdir()
    out = _both(_file(tmp_path / "b", _frames(10, one_missing)))
    assert out["energy:dft"].dtype == np.float64 and np.isnan(out["energy:dft"][8])


def test_text_values_go_through_python(tmp_path):
    def text_energy(i, f):
        if i in (2, 5):
            f["energy"] = "1_000"
        if i == 6:
            f["energy"] = "not a number"           # not stored: NaN
    out = _both(_file(tmp_path, _frames(8, text_energy)))
    assert out["energy:dft"][[2, 5]].tolist() == [1000.0, 1000.0] and np.isnan(out["energy:dft"][6])

    def all_text(i, f):
        f["energy"] = f"{i}_0"
    (tmp_path / "b").mkdir()
    out = _both(_file(tmp_path / "b", _frames(6, all_text)))
    assert out["energy:dft"].dtype == np.int64 and out["energy:dft"].tolist() == [i * 10 for i in range(6)]


def test_a_whole_number_beyond_64_bits_is_kept_as_np_array_keeps_it(tmp_path):
    def huge(i, f):
        f["energy"] = str(2**70 + i)
    out = _read(_file(tmp_path, _frames(4, huge)), batch=2).arrays["energy:dft"]
    python = _read(_file(tmp_path, _frames(4, huge)), fast=False).arrays["energy:dft"]
    assert out.dtype == python.dtype == object and out.tolist() == python.tolist()


def test_a_listed_per_atom_field_is_nan_where_a_frame_lacks_it(tmp_path):
    def weight(i, f):
        if i % 3 == 0:
            f["weight"] = [0.5 * a for a in range(len(f["numbers"]))]
    out = _both(_file(tmp_path, _frames(9, weight)))
    assert np.isnan(out["atom:weight"][3:7]).all() and out["atom:weight"][1] == 0.5


def test_a_listed_field_no_kept_frame_has_is_left_out(tmp_path):
    # contents() lists weight; the kept frames have none: parse would not give it.
    out = _both(_file(tmp_path, _frames(6)))
    assert "atom:weight" not in out


def test_a_per_atom_field_contents_did_not_list_is_placed(tmp_path):
    def late(i, f):
        if i >= 5:
            f["late"] = [float(i)] * len(f["numbers"])
    out = _both(_file(tmp_path, _frames(9, late)))
    first = int(out["n_atoms"][:5].sum())
    assert np.isnan(out["atom:late"][:first]).all() and out["atom:late"][first] == 5.0


def test_a_stride_gives_parse_into_only_the_kept_frames(tmp_path):
    path = _file(tmp_path, _frames(12))
    out = _both(path, stride=4)
    assert out["energy:dft"].tolist() == [0, 40, 80]
    assert sum(n for _, n, _ in ToyFastReader.calls) == 3


def test_groups_are_a_fiftieth_of_the_frames_and_at_most_group_bytes(monkeypatch):
    assert text._groups(np.full(500, 10)) == [(a, a + 10) for a in range(0, 500, 10)]
    assert text._groups(np.full(7, 10)) == [(a, a + 1) for a in range(7)]
    monkeypatch.setattr(text, "GROUP_BYTES", 25)
    assert text._groups(np.full(6, 10), batch=5) == [(0, 2), (2, 4), (4, 6)]
    assert text._groups(np.array([40, 10, 10]), batch=5) == [(0, 1), (1, 3)]   # a big frame alone


def test_progress_moves_per_group_and_a_cancel_stops_the_reading(tmp_path):
    path = _file(tmp_path, _frames(12))
    seen = []
    _read(path, batch=3, progress=lambda done, total: seen.append((done, total)))
    assert seen == [(3, 12), (6, 12), (9, 12), (12, 12)]

    class Cancelled(BaseException):               # as a cancelled task's error is
        pass

    def cancel(done, total):
        if done >= 6:
            raise Cancelled
    ToyFastReader.calls.clear()
    with pytest.raises(Cancelled):
        _read(path, batch=3, progress=cancel)
    assert len(ToyFastReader.calls) == 2 and ToyTextReader.parsed == []


def test_the_memory_check_refuses_before_reading_and_stops_midway(tmp_path, monkeypatch):
    path = _file(tmp_path, _frames(60))
    monkeypatch.setattr(text, "available_memory", lambda: 2000)
    with pytest.raises(NotEnoughMemory, match=r"a.toyq needs about .* of memory to load "
                                              r"\(\d+ atoms in 60 frames\), but .* is free"):
        _read(path)
    assert ToyFastReader.calls == []
    free = iter([10**12] + [text.MEMORY_FLOOR // 2] * 100)
    monkeypatch.setattr(text, "available_memory", lambda: next(free))
    with pytest.raises(NotEnoughMemory, match=r"Stopped reading a.toyq after 5 of 60 frames"):
        _read(path, batch=5)


def test_notfast_from_parse_into_hands_the_file_to_parse(tmp_path, caplog):
    def refuse(i, f):
        if i == 7:
            f["fast"] = "no"
    path = _file(tmp_path, _frames(10, refuse))
    with caplog.at_level(logging.WARNING, logger="FFAST"):
        out = _read(path, batch=3).arrays
    assert ToyTextReader.parsed                                  # the Python parser read it
    assert "a.toyq: read with the slower Python parser (frame 7: the toy says no)" in caplog.text
    _same(out, _read(path, fast=False).arrays)


def test_notfast_from_parse_after_the_handover_passes_up(tmp_path, monkeypatch):
    def refuse(data, first_frame):
        raise NotFast("columns change")
    monkeypatch.setattr(ToyFastReader, "parse", staticmethod(refuse))
    path = _file(tmp_path, _frames(4, lambda i, f: f.update(fast="no")))
    with pytest.raises(NotFast, match="columns change"):
        _read(path)


def test_without_the_compiled_package_parse_reads(tmp_path):
    _read(_file(tmp_path, _frames(5)), fast=False)
    assert ToyFastReader.calls == [] and ToyTextReader.parsed


def test_the_read_once_cache_wraps_the_fast_parser(tmp_path):
    path = _file(tmp_path, _frames(8))
    first = chunks.read_arrays(ToyFastReader, path, stride=2)
    n = len(ToyFastReader.calls)
    second = chunks.read_arrays(ToyFastReader, path, stride=2)
    assert len(ToyFastReader.calls) == n
    _same(first.arrays, second.arrays)


def test_fast_parser_usable_follows_the_package_and_its_contract(monkeypatch):
    import sys
    import types
    monkeypatch.undo()                            # the real function, not the fixture's
    for contract, usable in ((text.FAST_CONTRACT, True), (text.FAST_CONTRACT + 1, False)):
        monkeypatch.setitem(sys.modules, "ffast_xyz", types.SimpleNamespace(CONTRACT=contract))
        monkeypatch.setattr(text, "_fast_usable", None)
        assert text.fast_parser_usable() is usable
    monkeypatch.setitem(sys.modules, "ffast_xyz", None)          # import fails
    monkeypatch.setattr(text, "_fast_usable", None)
    assert text.fast_parser_usable() is False


# ── the warnings (ADR 0100, decision 5) ────────────────────────────────────

def _popups(caplog):
    return [r for r in caplog.records
            if r.levelno >= logging.WARNING and getattr(r, "popup", True) is not False]


@pytest.mark.parametrize("missing, words", [(True, "Fast reader not installed"),
                                            (False, "too old for this FFAST")])
def test_no_usable_package_pops_up_once_per_start_not_per_file(tmp_path, monkeypatch, caplog, missing, words):
    monkeypatch.setattr(text, "_fast_missing", missing)
    monkeypatch.setattr(text, "_fast_warned", False)
    path = _file(tmp_path, _frames(5))
    with caplog.at_level(logging.WARNING, logger="FFAST"):
        _read(path, fast=False)
        _read(path, fast=False)
    popups = _popups(caplog)
    assert len(popups) == 1
    assert words in popups[0].getMessage() and "`ffast install-fast-reader`" in popups[0].getMessage()
    assert ToyTextReader.parsed                                  # the Python parser still read it


def test_a_reader_without_parse_into_gives_no_popup(tmp_path, monkeypatch, caplog):
    monkeypatch.setattr(text, "_fast_warned", False)
    monkeypatch.setattr(text, "fast_parser_usable", lambda: False)
    with caplog.at_level(logging.WARNING, logger="FFAST"):
        chunks.read_arrays(ToyTextReader, write_toy(tmp_path / "b.toyt", _frames(4)), use_cache=False)
    assert _popups(caplog) == []


def test_a_usable_package_gives_no_popup(tmp_path, monkeypatch, caplog):
    monkeypatch.setattr(text, "_fast_warned", False)
    with caplog.at_level(logging.WARNING, logger="FFAST"):
        _read(_file(tmp_path, _frames(5)))
    assert _popups(caplog) == []


def test_a_handed_over_file_warns_in_the_log_and_the_status_without_a_popup(tmp_path, monkeypatch, caplog):
    monkeypatch.setattr(text, "_fast_warned", False)
    seen = []
    path = _file(tmp_path, _frames(10, lambda i, f: f.update(fast="no") if i == 7 else None))
    with caplog.at_level(logging.WARNING, logger="FFAST"):
        _read(path, batch=3, on_handover=seen.append)
    note = "a.toyq: read with the slower Python parser (frame 7: the toy says no)"
    assert seen == [note]
    assert note in caplog.text and _popups(caplog) == []


def test_the_status_keeps_the_handover_in_the_progress_text_that_follows():
    from ffast.core.loading_coordinator import LoadingCoordinator
    sent = []
    fake = types.SimpleNamespace(_progress=lambda task, msg, **kw: sent.append(msg))
    report = LoadingCoordinator._readProgress(fake, "/x/a.xyz", 7)
    report(1, 10)
    report.handed_over("a.xyz: read with the slower Python parser (why)")
    report(10, 10)
    assert sent == ["Reading a.xyz: 1 of 10 frames…",
                    "a.xyz: read with the slower Python parser (why)…",
                    "a.xyz: read with the slower Python parser (why): 10 of 10 frames…"]
