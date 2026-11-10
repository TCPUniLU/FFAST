"""The reader live runs and scored items share (ADR 0093 step 3)."""

from __future__ import annotations

import numpy as np
import pytest

from ffast.live import run_folder as rf
from ffast.live.analysis_reader import AnalysisReader


class _Data:
    N = 3
    steps = [0, 10, 20]

    def getCoordinates(self, i):
        return np.zeros((2, 3))


class _Reader(AnalysisReader):
    run_id = "r"
    fingerprint = "live:r"

    def __init__(self):
        self.dataset = _Data()
        self._init_analysis()


def _folder(tmp_path):
    a = tmp_path / rf.ANALYSIS_DIR / "a1"
    a.mkdir(parents=True)
    rf.write_json_atomic(a / rf.SETTINGS, {
        "scores_format": 2, "role": "centroid", "tests": [["m", "T2"]],
        "thresholds": {"m": {"T2": {"centroid": 2.0}}}})
    rf.ScoresWriter(a / rf.SCORES).append([
        {"segment": 0, "frame": 0, "step": 0, "time": 0.0, "exceedance": 0.5, "test": ["m", "T2"],
         "marked": False, "tracks": {"m": {"T2": 1.0, "SPE": None}}, "chem": {"broken": 0, "close": 0}},
        {"segment": 0, "frame": 1, "step": 10, "time": None,
         "refused": "atom order differs from the bundle at atom 0: H (expected C)"},
        {"segment": 0, "frame": 2, "step": 20, "time": 1.0, "exceedance": 1.5, "test": ["m", "T2"],
         "marked": True, "tracks": {"m": {"T2": 3.0, "SPE": None}}, "chem": {"broken": 1, "close": 0}},
    ])
    return tmp_path


def test_a_refused_frame_is_a_gap_and_keeps_the_references_words(tmp_path):
    r = _Reader()
    r.rows += r.read_analysis(_folder(tmp_path))
    assert np.array_equal(r.analysis_value(("worst",)), [0.5, np.nan, 1.5], equal_nan=True)
    assert np.array_equal(r.analysis_value(("score", "m", "T2")), [0.5, np.nan, 1.5], equal_nan=True)
    assert np.array_equal(r.analysis_value(("chem", "broken")), [0, np.nan, 1], equal_nan=True)
    assert r.analysis_reason(1).startswith("atom order differs")
    assert r.analysis_reason(0) is None and r.analysis_reason(2) is None


def test_attribution_of_a_refused_frame_says_why(tmp_path):
    r = _Reader()
    r.rows += r.read_analysis(_folder(tmp_path))
    with pytest.raises(ValueError, match="frame 1 was not scored: atom order differs"):
        r.attribution(1)
