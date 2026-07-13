"""Shared pick-toolbar registry contract (ADR 0039).

The five atom-pick tools used to each hang off a Select button buried in their
own sidebar pane, all driving the single `InteractiveCanvas.activeAtomSelectTool`
slot with no visible owner. ADR 0039 consolidates them onto one flat pick
toolbar built from every registered `ClientFeature.tool_class`, and each tool
declares a short `toolbarName` (its button label) and a `paneName` (the pane
auto-expanded when the tool is armed).

These tests pin the registry contract without a QApplication: every pick module
must contribute a tool_class carrying both bits of metadata (a regression guard
for loupeBonds, which previously omitted tool_class entirely), and the canvas's
tool-collection helper must de-duplicate and preserve load order.
"""
import importlib

import pytest

PICK_MODULES = [
    "modules.loupe.loupeAtomFilter",
    "modules.loupe.loupeBonds",
    "modules.loupe.loupeAtomAlign",
    "modules.loupe.loupeForceVectors",
    "modules.loupe.loupeInfoSelect",
]


def _tool_classes(module_name):
    mod = importlib.import_module(module_name)
    return [f.tool_class for f in mod.CLIENT_FEATURES if f.tool_class is not None]


@pytest.mark.parametrize("module_name", PICK_MODULES)
def test_pick_module_contributes_tool_with_toolbar_metadata(module_name):
    tools = _tool_classes(module_name)
    assert tools, f"{module_name} declares no pick tool_class"
    for tc in tools:
        assert tc.toolbarName, f"{tc.__name__} missing toolbarName (ADR 0039)"
        # paneName is optional: None means the tool has no sidebar pane to
        # expand (e.g. Atoms Info, whose readout lives in the pick strip).


def test_bonds_feature_wires_tool_class():
    """Regression: BondSelect was defined but never registered, so bond picking
    had a Select button but would never appear on the shared toolbar."""
    from modules.loupe.loupeBonds import BondSelect
    assert BondSelect in _tool_classes("modules.loupe.loupeBonds")


def test_registered_tool_classes_dedupes_and_keeps_order():
    """_registeredToolClasses reads the handler's client_features, drops
    features without a tool_class and duplicate classes, and preserves order."""
    from UI.loupe.canvas import InteractiveCanvas
    from UI.clientFeatures import ClientFeature

    class A: pass
    class B: pass

    handler = type("H", (), {})()
    handler.client_features = [
        ClientFeature(stage_id="s1"),                 # no tool -> skipped
        ClientFeature(stage_id="s2", tool_class=A),
        ClientFeature(stage_id="s3", tool_class=B),
        ClientFeature(stage_id="s4", tool_class=A),   # dup -> skipped
    ]

    canvas = InteractiveCanvas.__new__(InteractiveCanvas)  # no Qt widget tree
    canvas.loupe = type("L", (), {"handler": handler})()

    assert canvas._registeredToolClasses() == [A, B]


class _FakeSettings:
    def __init__(self, d):
        self.d = dict(d)

    def get(self, k, default=None):
        return self.d.get(k, default)

    def setParameter(self, k, v, refresh=False):
        self.d[k] = v


def _bond_tool(settings, dynamic_bonds=None, index=0):
    """A BondSelect wired to fake settings + a dataset returning dynamic bonds,
    skipping the Qt-touching AtomSelectionBase.__init__."""
    from modules.loupe.loupeBonds import BondSelect

    dataset = type("D", (), {"getBondIndices": lambda self, i: dynamic_bonds or []})()
    loupe = type("L", (), {
        "settings": settings,
        "index": index,
        "getSelectedDataset": lambda self: dataset,
    })()
    tool = BondSelect.__new__(BondSelect)
    tool.canvas = type("C", (), {"loupe": loupe})()
    return tool


def test_bond_select_first_pick_does_not_crash_on_none_indices():
    """Regression: fixedBondIndices defaults to None, so the first picked bond
    hit set(None) and the selection silently did nothing."""
    settings = _FakeSettings({"fixedBondIndices": None})
    tool = _bond_tool(settings)  # no dynamic bonds → empty seed
    tool.selectedPoints = [0, 1]

    tool.selectCallback()

    assert settings.get("fixedBondIndices") == [(0, 1)]


def test_bond_select_seeds_from_dynamic_bonds_and_erases_one():
    """Picking an existing (dynamic) bond removes just that bond, keeping the
    rest — the fixed set is seeded from the currently-shown bonds first."""
    settings = _FakeSettings({"fixedBondIndices": None})
    tool = _bond_tool(settings, dynamic_bonds=[[0, 1], [1, 2], [2, 3]])
    tool.selectedPoints = [2, 1]  # pick the existing 1–2 bond (any order)

    tool.selectCallback()

    assert set(settings.get("fixedBondIndices")) == {(0, 1), (2, 3)}


def test_bond_select_adds_new_pair_to_existing_fixed_set():
    settings = _FakeSettings({"fixedBondIndices": [(0, 1)]})
    tool = _bond_tool(settings)
    tool.selectedPoints = [3, 2]

    tool.selectCallback()

    assert set(settings.get("fixedBondIndices")) == {(0, 1), (2, 3)}


def test_atom_ids_to_displayed_identity_and_mapped():
    import numpy as np
    from ffast.renderers.vispy.adapter import VispySceneAdapter

    a = VispySceneAdapter.__new__(VispySceneAdapter)
    a._atom_positions = np.zeros((3, 3))

    a._atom_ids = None  # identity mapping, out-of-range dropped
    assert a._atom_ids_to_displayed([0, 2]) == [0, 2]
    assert a._atom_ids_to_displayed([5]) == []

    a._atom_ids = [10, 20, 30]  # id → displayed index, unknown dropped
    assert a._atom_ids_to_displayed([30, 10]) == [2, 0]
    assert a._atom_ids_to_displayed([99]) == []


def test_set_pick_highlight_empty_hides_without_creating_visual():
    from ffast.renderers.vispy.adapter import VispySceneAdapter

    a = VispySceneAdapter.__new__(VispySceneAdapter)
    a._atom_positions = None
    a._pick_visual = None

    a.set_pick_highlight(None)  # nothing selected → no vispy visual constructed

    assert a._pick_visual is None
