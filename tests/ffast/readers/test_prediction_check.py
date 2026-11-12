"""One check for every prediction path (ADR 0095, decision 6): frame count,
atoms per frame and elements are refused, positions and units only warn. The
message names the first frame that differs."""
import logging

import numpy as np
import pytest

from ffast.readers import chunks
from ffast.readers.dataset import ReaderDataset
from ffast.readers.match import Facts, PredictionRefused, check_prediction
from tests.ffast.readers.toy_reader import ToyReader, make_frames, write_toy


def _dataset(tmp_path, frames=None, stride=0):
    path = write_toy(tmp_path / "ds.toy", frames or make_frames(6, variable=True))
    return ReaderDataset(path, chunks.read_arrays(ToyReader, path, stride=stride), "toy")


def _facts(ds, **kw):
    """The facts of a prediction that is the dataset's own file, with changes."""
    counts = np.diff(ds.molecule_offsets)
    base = dict(name="p.toy", n_kept=ds.N, n_file_frames=getattr(ds, "sourceFrameCount", None),
                counts=counts, numbers=ds.z_flat, positions=ds.R_flat, units=None)
    base.update(kw)
    return Facts(**base)


def test_the_datasets_own_arrays_pass_with_no_warning(toy, tmp_path):
    assert check_prediction(_dataset(tmp_path), _facts(_dataset(tmp_path))) == []


def test_a_different_file_frame_count_is_refused_with_both_counts(toy, tmp_path):
    ds = _dataset(tmp_path)
    with pytest.raises(PredictionRefused) as e:
        check_prediction(ds, _facts(ds, n_file_frames=5))
    assert str(e.value) == "Prediction 'p.toy' has 5 frames, but the dataset's file has 6."


def test_without_a_recorded_source_count_the_kept_counts_are_compared(toy, tmp_path):
    ds = _dataset(tmp_path)
    del ds.sourceFrameCount
    with pytest.raises(PredictionRefused) as e:
        check_prediction(ds, _facts(ds, n_file_frames=None, n_kept=4))
    assert str(e.value) == "Frame count mismatch for prediction 'p.toy'. Expected 6 frames, got 4."


def test_atoms_per_frame_name_the_first_frame_that_differs(toy, tmp_path):
    ds = _dataset(tmp_path)                          # 3 4 5 3 4 5
    counts = np.diff(ds.molecule_offsets).copy()
    counts[3], counts[4] = 9, 9
    with pytest.raises(PredictionRefused) as e:
        check_prediction(ds, _facts(ds, counts=counts, numbers=None, positions=None))
    assert str(e.value) == ("Atom count mismatch for prediction 'p.toy': frame 3 has 9 atoms, "
                            "the dataset's frame 3 has 3.")


def test_a_strided_dataset_names_the_files_frame_number(toy, tmp_path):
    ds = _dataset(tmp_path, make_frames(10, variable=True), stride=3)   # file frames 0 3 6 9
    counts = np.diff(ds.molecule_offsets).copy()
    counts[2] += 1
    with pytest.raises(PredictionRefused, match="frame 6 has"):
        check_prediction(ds, _facts(ds, counts=counts, numbers=None, positions=None))


def test_an_element_that_differs_is_named_with_its_frame_and_atom(toy, tmp_path):
    ds = _dataset(tmp_path, make_frames(3))
    numbers = ds.z_flat.copy()
    numbers[5] = 6                                   # frame 1, atom 2: H -> C
    with pytest.raises(PredictionRefused) as e:
        check_prediction(ds, _facts(ds, numbers=numbers))
    assert str(e.value) == ("Element mismatch for prediction 'p.toy': in frame 1, atom 2 is C "
                            "but the dataset's is H.")


def test_a_file_without_numbers_or_positions_skips_those_checks(toy, tmp_path):
    ds = _dataset(tmp_path)
    assert check_prediction(ds, _facts(ds, numbers=None, positions=None)) == []


def test_positions_that_differ_by_more_than_a_thousandth_warn_not_refuse(toy, tmp_path, caplog):
    ds = _dataset(tmp_path, make_frames(3))
    moved = ds.R_flat.copy()
    moved[4, 0] += 0.01                              # frame 1
    with caplog.at_level(logging.WARNING, logger="FFAST"):
        problems = check_prediction(ds, _facts(ds, positions=moved))
    text = ("Prediction 'p.toy': positions differ from the dataset's by up to 0.0100 Å "
            "(first in frame 1). A file may round its positions.")
    assert problems == [text]
    assert [r.getMessage() for r in caplog.records if r.levelno == logging.WARNING] == [text]


def test_positions_within_a_thousandth_are_quiet(toy, tmp_path):
    ds = _dataset(tmp_path, make_frames(3))
    assert check_prediction(ds, _facts(ds, positions=ds.R_flat + 0.0005)) == []


def test_a_unit_label_that_differs_warns_and_one_the_dataset_lacks_does_not(toy, tmp_path):
    ds = _dataset(tmp_path)
    assert check_prediction(ds, _facts(ds, units={"energy": "Hartree"})) == []   # no dataset unit
    ds.setUnits({"energy": "eV"})
    assert check_prediction(ds, _facts(ds, units={"energy": "eV"})) == []
    assert check_prediction(ds, _facts(ds, units={"energy": "Hartree"})) == [
        "Prediction 'p.toy': its energy unit is Hartree, the dataset's is eV. "
        "FFAST does not convert units."]


def test_a_dataset_without_offsets_still_gets_the_frame_count_check():
    class Bare:
        def getN(self):
            return 10

    check_prediction(Bare(), Facts(name="m", n_kept=10))
    with pytest.raises(PredictionRefused, match="Expected 10 frames, got 7"):
        check_prediction(Bare(), Facts(name="m", n_kept=7))
