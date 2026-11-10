"""The scoring core on its own, with no run folder and no dump (ADR 0093 step 1)."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pytest

from ffast.live import run_folder as rf
from ffast.live.analysis_writer import AnalysisWriter
from tests.ffast.live.bundle_helpers import initial_atoms, make_bundle
from tests.ffast.live.test_scorer import _quiet, _stretch


@pytest.fixture(scope="module")
def bundle_dir(tmp_path_factory):
    return make_bundle(tmp_path_factory.mktemp("b"))[0]


@dataclass
class Frame:
    index: int
    step: int
    time: float | None
    positions: np.ndarray


def _writer(tmp_path, bundle_dir, events, positions, persist_ps=0.5):
    import detectana

    bundle = detectana.load(bundle_dir)
    return AnalysisWriter(
        tmp_path / "analysis" / "a1", bundle, bundle_dir=bundle_dir, bundle_sha="sha",
        detectana_installed=detectana.__version__, settings={"persist_ps": persist_ps},
        persist_ps=persist_ps, event=lambda kind, **f: events.append({"kind": kind, **f}),
        positions_of=lambda i: positions[i], base_dir=tmp_path)


def test_frames_without_a_run_folder_get_the_rows_the_bundle_scores(tmp_path, bundle_dir):
    import detectana

    base = initial_atoms().get_positions()
    rng = np.random.default_rng(3)
    positions = [base + rng.normal(0, 0.02, base.shape) for _ in range(4)]
    events: list[dict] = []
    w = _writer(tmp_path, bundle_dir, events, positions)
    w.write_settings()
    w.identity = "unverified"
    w.score([Frame(i, i * 10, i * 0.1, p) for i, p in enumerate(positions)])

    rows = rf.read_scores(w.analysis_dir / rf.SCORES)
    bundle = detectana.load(bundle_dir)
    want = bundle.score(np.stack(positions))
    exc = bundle.exceedance(want, role="centroid")
    assert [r["frame"] for r in rows] == [0, 1, 2, 3]
    assert [r["identity"] for r in rows] == ["unverified"] * 4
    for track, values in want.items():
        for t in ("T2", "SPE"):
            if values.get(t) is not None:
                assert [r["tracks"][track][t] for r in rows] == pytest.approx(list(values[t]))
    assert [r["exceedance"] for r in rows] == pytest.approx(list(exc.values))
    assert (w.analysis_dir / rf.SETTINGS).exists()


def test_a_stretched_bond_raises_an_alarm_with_a_report(tmp_path, bundle_dir):
    positions = _quiet(60)
    positions[30:] = [_stretch(p, 0.6) for p in positions[30:]]
    events: list[dict] = []
    seen: list = []
    w = _writer(tmp_path, bundle_dir, events, positions)
    w.on_alarm = seen.append
    w.write_settings()
    w.identity = "unverified"
    w.score([Frame(i, i * 100, i * 0.1, p) for i, p in enumerate(positions)])

    [alarm] = [e for e in events if e["kind"] == "alarm"]
    assert alarm["alarm"] == "ood" and alarm["onset"]["frame"] == 30
    assert len(seen) == 1 and seen[0].kind == "ood"
    report = rf.read_json(tmp_path / alarm["report"])
    assert report["kind"] == "ood" and "attribution" in report
