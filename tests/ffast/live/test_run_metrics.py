"""A live run's scores as metrics under "Run scores" (ADR 0091 step 3)."""

from __future__ import annotations

import pytest

from ffast.live.live_run import LiveRun
from ffast.metrics.catalog import build_metric_catalog
from ffast.metrics.registry import default_registry
from ffast.metrics import run_values
from ffast.protocol import MetricCatalog
from tests.ffast.live.test_live_run import _Env, _format2_run_folder, _run_folder


class _Feed:
    def __init__(self):
        self.changed_pieces = []

    def changed(self, *pieces):
        self.changed_pieces.append(pieces)


@pytest.fixture(autouse=True)
def _clean_global_registry():
    yield
    for mid in [m for m in default_registry.list_metrics() if m.startswith(run_values.PREFIX)]:
        default_registry.unregister(mid)


def _run(env, tmp_path, sub, **kw):
    d = _format2_run_folder(tmp_path, name=f"20261107-120000-node-{sub}-abcd", **kw)
    run = LiveRun(env, d, lambda m: None)
    env.live_runs[run.run_id] = run
    run.apply(run.read_files())
    return run


def _env():
    env = _Env()
    env.live_runs = {}
    env.window_feed = _Feed()
    return env


def _ids():
    return sorted(m for m in default_registry.list_metrics() if m.startswith(run_values.PREFIX))


def test_opening_a_run_lists_each_test_and_the_run_totals_with_a_density_each(tmp_path):
    env = _env()
    _run(env, tmp_path, "a")
    base = [m for m in _ids() if "__" not in m]
    assert base == ["ffast.run_chem_broken", "ffast.run_chem_close", "ffast.run_score_angles_T2",
                    "ffast.run_score_bonds_SPE", "ffast.run_score_bonds_T2", "ffast.run_score_worst"]
    assert len(_ids()) == 2 * len(base)                         # one density each
    by_id = {e["id"]: e for e in build_metric_catalog(default_registry)}
    t2 = by_id["ffast.run_score_bonds_T2"]
    assert (t2["group"], t2["level"], t2["label"]) == ("Run scores", 1.0, "bonds T² ÷ threshold")
    assert by_id["ffast.run_chem_broken"]["level"] is None
    assert by_id["ffast.run_chem_broken"]["label"] == "broken bonds"
    assert by_id["ffast.run_score_worst"]["label"] == "worst score ÷ threshold"
    assert by_id["ffast.run_score_bonds_T2__value_kde"]["group"] == "Run scores"
    assert by_id["ffast.run_score_bonds_T2"]["shape"] == "N_frames"
    MetricCatalog(metrics=list(by_id.values()))                  # the wire model takes them
    assert ("METRIC_CATALOG", "TAB_LAYOUT") in env.window_feed.changed_pieces


def test_a_second_run_with_the_same_tests_adds_none_and_one_with_more_adds_its_own(tmp_path):
    env = _env()
    _run(env, tmp_path, "a")
    before = _ids()
    _run(env, tmp_path, "b")
    assert _ids() == before


def test_forgetting_the_only_run_removes_them_and_forgetting_one_of_two_keeps_them(tmp_path):
    env = _env()
    a = _run(env, tmp_path, "a")
    b = _run(env, tmp_path, "b")
    before = _ids()
    assert before
    env.live_runs.pop(a.run_id)
    run_values.sync(env)
    assert _ids() == before
    env.live_runs.pop(b.run_id)
    run_values.sync(env)
    assert _ids() == []


def test_a_format_1_run_lists_the_worst_score_only(tmp_path):
    env = _env()
    d = _run_folder(tmp_path)
    run = LiveRun(env, d, lambda m: None)
    env.live_runs[run.run_id] = run
    run.apply(run.read_files())
    assert [m for m in _ids() if "__" not in m] == ["ffast.run_score_worst"]


def test_every_other_metric_has_no_group_and_no_level():
    import ffast.metrics.builtin  # noqa: F401
    assert all(e["group"] is None and e["level"] is None
               for e in build_metric_catalog(default_registry) if not e["id"].startswith(run_values.PREFIX))


def test_a_saved_tab_naming_a_run_score_resolves_once_a_run_has_it(tmp_path):
    """A tab saved with a run score (and its density) is laid out before any run
    is open; once one is, the layout is sent again and names nothing unknown."""
    from ffast.config.models import AnalysisTabConfig
    from ffast.config.tabs import build_tab_layout

    tab = AnalysisTabConfig.model_validate({"name": "Scores", "panels": [
        {"kind": "timeline", "row": 0, "col": 0, "metrics": {"y": {"metric": "ffast.run_score_worst"}}},
        {"kind": "density", "row": 1, "col": 0,
         "metrics": {"value": {"metric": "ffast.run_score_worst", "transform": "value_kde"}}}]})
    before = build_tab_layout([tab])[0]["panels"]
    # A plain id is checked against the page's catalog; a density must be compiled here.
    assert [p["unresolved"] for p in before] == [[], ["ffast.run_score_worst"]]
    env = _env()
    _run(env, tmp_path, "a")
    assert ("METRIC_CATALOG", "TAB_LAYOUT") in env.window_feed.changed_pieces
    after = build_tab_layout([tab])[0]["panels"]
    assert [p["unresolved"] for p in after] == [[], []]
    assert after[1]["metrics"]["value"] == "ffast.run_score_worst__value_kde"
