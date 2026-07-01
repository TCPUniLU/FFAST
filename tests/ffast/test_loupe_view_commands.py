"""Client-side view-command batching.

A single Loupe action often emits several version-gated commands (force vectors
sends a feature toggle plus five parameters). The server applies commands in
order and bumps the view version by one per accepted scientific command, so a
batch must carry *successive* versions. Regression: every command in a batch was
stamped with the same `_sceneVersion`, so the server accepted the first and
rejected the rest as STALE_VERSION — the force-vector "filter to selection"
(and atom_align, fixed bonds) silently did nothing because their parameters
never landed.

These tests drive the real Loupe methods on a duck-typed instance (no Qt widget
construction) and replay the recorded commands through the real server
VisualizationView, asserting all are accepted and the parameters land.
"""
import pytest
from pydantic import TypeAdapter

from ffast.visualization.commands import ViewCommand
from ffast.visualization.view import VisualizationView


from tests.ffast._env_facets import _attach_env_facets


class _RecordingEnv:
    def __init__(self):
        self.sent = []
        _attach_env_facets(self)  # ADR 0020 sub-objects (env.remote.sendViewCommand)

    def sendViewCommand(self, **fields):
        self.sent.append(fields)


class _FakeSettings(dict):
    def get(self, key, default=None):
        return super().get(key, default)


def _make_loupe(settings):
    """A Loupe instance with only the attributes the senders touch — no __init__,
    so no QApplication/widget tree is required."""
    from UI.loupe.window import Loupe
    obj = Loupe.__new__(Loupe)
    obj.env = _RecordingEnv()
    obj.viewId = "v1"
    obj._sceneVersion = 0
    obj.settings = settings
    return obj


def _replay(sent):
    """Apply recorded commands through the real server view, in order."""
    view = VisualizationView("v1")
    view.state.dataset_ref = "fp"
    ta = TypeAdapter(ViewCommand)
    results = [view.apply_command(ta.validate_python(c)) for c in sent]
    return view, results


def test_force_vectors_batch_uses_successive_versions_and_filter_lands():
    from UI.loupe.window import Loupe
    loupe = _make_loupe(_FakeSettings(
        showForceVectors=True, forceVectorsModelKey=None,
        forceVectorsLength=10, forceVectorsNormalised=True,
        forceVectorsFilterEnabled=True, forceVectorsAtomIndices=[1, 2],
    ))
    Loupe.onApplyForceVectors(loupe)

    sent = loupe.env.sent
    assert len(sent) == 6  # TOGGLE_FEATURE + 5 SET_PARAMETER
    # successive versions 0,1,2,... — not all the same stale version
    assert [c["view_version"] for c in sent] == list(range(6))

    view, results = _replay(sent)
    assert all(r.success for r in results)
    assert "forces" in view.state.enabled_features
    fa = view.state.parameters["ffast.force_arrows"]
    assert fa["filter_enabled"] is True
    assert fa["atom_indices"] == [1, 2]


def test_atom_align_batch_parameters_land():
    from UI.loupe.window import Loupe
    loupe = _make_loupe(_FakeSettings(
        alignAtoms=True, alignAtomsIndices="0 1 2", alignAtomsConfIndex=0,
    ))
    Loupe.onApplyAtomAlign(loupe)

    sent = loupe.env.sent
    assert [c["view_version"] for c in sent] == list(range(len(sent)))

    view, results = _replay(sent)
    assert all(r.success for r in results)
    assert "atom_align" in view.state.enabled_features
    aa = view.state.parameters["ffast.atom_align"]
    assert aa["atom_indices"] == [0, 1, 2]
    assert aa["reference_frame"] == 0


def test_fixed_bonds_batch_parameters_land():
    from UI.loupe.window import Loupe
    loupe = _make_loupe(_FakeSettings(
        bondType="Fixed", fixedBondIndices=[(0, 1), (1, 2)],
    ))
    Loupe.onApplyBonds(loupe)

    sent = loupe.env.sent
    assert [c["view_version"] for c in sent] == list(range(len(sent)))

    view, results = _replay(sent)
    assert all(r.success for r in results)
    bonds = view.state.parameters["ffast.bonds"]
    assert bonds["bond_type"] == "Fixed"
    assert bonds["fixed_indices"] == [[0, 1], [1, 2]]
