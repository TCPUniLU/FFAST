"""Atom coloring (ADR 0016).

Metric-based atom coloring was dead in the UI: modules/loupeAtoms.py filtered
colorable metrics with ``schema.shape == "per_structure_per_atom"``, but a
metric Shape is a tuple of Dim objects (e.g. ``(dims.N_atoms,)``), never that
string — so the metric list was always empty and the "Metric" coloring option,
its selector, and its parameter controls never appeared.

These tests lock (a) the corrected per-atom metric predicate, and (b) the
server-side value-driven coloring path, including that a metric Compute
Parameter actually changes the produced colors.
"""
import importlib.util
import os

import numpy as np
import pytest

from ffast.metrics.builtin import (  # noqa: F401 — registers builtin metrics
    accel_metrics, atomic_metrics, energy_metrics, force_metrics,
)
from ffast.metrics.registry import _default_registry as reg
from ffast.visualization.models import VisualizationState
from ffast.visualization.scene_builder import build_scene


def _load_loupe_atoms():
    """Load modules/loupe/loupeAtoms.py by file path (load it as a plugin would)."""
    root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    path = os.path.join(root, "modules", "loupe", "loupeAtoms.py")
    spec = importlib.util.spec_from_file_location("module_loupeAtoms_test", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class TestAtomColoringMetricSelection:
    def test_per_atom_metrics_are_offered(self):
        ids = _load_loupe_atoms()._atom_coloring_metric_ids(reg)
        assert "ffast.force_mae" in ids
        assert "ffast.accel_mae_per_atom" in ids

    def test_per_element_metrics_are_offered(self):
        # per-element metrics color atoms by broadcasting the element's value.
        ids = _load_loupe_atoms()._atom_coloring_metric_ids(reg)
        assert "ffast.force_mae_per_element" in ids
        assert "ffast.accel_mae_per_element" in ids

    def test_non_colorable_metrics_excluded(self):
        ids = _load_loupe_atoms()._atom_coloring_metric_ids(reg)
        assert "ffast.force_mae_global" not in ids    # scalar
        assert "ffast.force_rmse" not in ids           # per-frame
        assert "ffast.force_difference" not in ids      # vector per atom
        assert "ffast.energy_mae" not in ids            # scalar

    def test_old_string_predicate_matched_nothing(self):
        # Documents the bug: shapes are Dim tuples, never the literal string.
        assert all(
            reg.get(mid)[0].shape != "per_structure_per_atom"
            for mid in reg.list_metrics()
        )


class TestMetricDisplayNames:
    """Metrics carry a display name used directly as the Coloring combo label
    (ADR 0016), replacing the hardcoded friendly-label dictionaries."""

    def test_atom_colorable_metrics_have_labels(self):
        assert reg.get("ffast.force_mae")[0].label == "Force Error (per atom)"
        assert reg.get("ffast.accel_mae_per_atom")[0].label == "Acceleration Error"

    def test_metric_color_label_falls_back_to_id(self):
        mod = _load_loupe_atoms()
        # operates on catalog entries (plain dicts)
        assert mod.metric_color_label({"label": "Force Error", "id": "ffast.force_mae"}) == "Force Error"
        assert mod.metric_color_label({"label": "", "id": "x.y"}) == "x.y"


class TestMetricCatalog:
    """Server builds a serializable catalog; the client builds its metric UI from
    it instead of its own registry (ADR 0016)."""

    def test_catalog_includes_metrics_with_labels_and_shapes(self):
        from ffast.metrics.catalog import build_metric_catalog
        cat = {e["id"]: e for e in build_metric_catalog(reg)}
        assert cat["ffast.force_mae"]["label"] == "Force Error (per atom)"
        assert cat["ffast.force_mae"]["shape"] == "N_atoms"
        assert cat["ffast.force_mae_per_element"]["shape"] == "N_elements"
        assert cat["ffast.energy_mae"]["shape"] == "scalar"
        # parameters are plain, transport-safe dicts
        assert cat["ffast.force_mae"]["parameters"]["norm"]["choices"] == ["l1", "l2"]

    def test_catalog_entries_are_plain_dicts(self):
        from ffast.metrics.catalog import build_metric_catalog
        for e in build_metric_catalog(reg):
            assert set(e) >= {"id", "label", "shape", "unit", "parameters"}
            assert isinstance(e["parameters"], dict)

    def test_client_builds_colorable_set_from_catalog(self):
        """The client filters the server catalog to atom-colorable metrics —
        not its own registry."""
        import types
        from ffast.metrics.catalog import build_metric_catalog
        mod = _load_loupe_atoms()
        catalog = {e["id"]: e for e in build_metric_catalog(reg)}
        loupe = types.SimpleNamespace(env=types.SimpleNamespace(metricCatalog=catalog))
        ids = {e["id"] for e in mod._colorable_metric_entries(loupe)}
        assert "ffast.force_mae" in ids
        assert "ffast.force_mae_per_element" in ids
        assert "ffast.energy_mae" not in ids   # scalar excluded

    def test_server_replay_enqueues_metric_catalog(self):
        """The server's state replay packs a METRIC_CATALOG message (and
        registers built-ins first)."""
        from ffast.session.server_session import ServerSession
        from ffast.protocol.rpc import unpack

        class _Out:
            def __init__(self):
                self.sent = []

            def put_nowait(self, data):
                self.sent.append(data)

        out = _Out()
        # _replay_metric_catalog is env-independent (reads the global registry).
        ServerSession(None, out)._replay_metric_catalog()
        assert out.sent, "no METRIC_CATALOG enqueued"
        event, _, kwargs = unpack(out.sent[-1])
        assert event == "METRIC_CATALOG"
        ids = {m["id"] for m in kwargs["metrics"]}
        assert "ffast.force_mae" in ids
        assert "ffast.accel_mae_per_atom" in ids   # registered via the replay

    def test_server_loads_external_metric_modules(self, tmp_path):
        """(A) The server loads project-config Trusted Metric Modules at startup,
        so external metrics register and flow into the catalog."""
        import server
        from ffast.metrics.catalog import build_metric_catalog

        (tmp_path / "ext_metrics.py").write_text(
            "from ffast.metrics.registry import metric\n"
            "from ffast.metrics import dims\n"
            "import numpy as np\n"
            "@metric(id='test.server_ext', label='Server Ext',\n"
            "        inputs={'x': 'frame.positions'}, shape=(dims.N_atoms,), unit='eV')\n"
            "def server_ext(x):\n"
            "    return np.zeros(len(x))\n"
        )
        (tmp_path / "ffast.toml").write_text('[[metrics.modules]]\npath = "ext_metrics.py"\n')

        server._load_project_metric_modules(str(tmp_path / "ffast.toml"))

        cat = {e["id"]: e for e in build_metric_catalog(reg)}
        assert "test.server_ext" in cat
        assert cat["test.server_ext"]["label"] == "Server Ext"
        assert cat["test.server_ext"]["shape"] == "N_atoms"   # colorable


class TestColorSourceResolution:
    """Single source of truth for the Coloring combo (ADR 0016).

    Each combo label maps to exactly one server color source, so the coloring
    modules can no longer clobber each other's ``atomColorSource``. Metric
    colorings appear by display name and map straight to ``metric:<id>``.
    """

    SOURCE_MAP = {
        "Elements": "element",
        "Force Error": "metric:ffast.force_mae",
        "Acceleration Error": "metric:ffast.accel_mae_per_atom",
        "Displacement": "displacement",
    }

    def _resolve(self):
        return _load_loupe_atoms()._resolve_color_source

    def test_each_label_maps_to_its_own_source(self):
        resolve = self._resolve()
        assert resolve("Elements", self.SOURCE_MAP) == "element"
        assert resolve("Force Error", self.SOURCE_MAP) == "metric:ffast.force_mae"
        assert resolve("Acceleration Error", self.SOURCE_MAP) == "metric:ffast.accel_mae_per_atom"
        # The regression: displacement used to resolve to "element".
        assert resolve("Displacement", self.SOURCE_MAP) == "displacement"

    def test_unknown_label_falls_back_to_element(self):
        resolve = self._resolve()
        assert resolve("Nonsense", self.SOURCE_MAP) == "element"


class _FakeColorSettings(dict):
    def get(self, key, default=None):
        return super().get(key, default)

    def setParameter(self, key, value, refresh=False):
        self[key] = value


from tests.ffast._env_facets import _attach_env_facets


class _FakeColorEnv:
    """Minimal env exposing one model with a cached force prediction for DS."""
    DS = "ds-fp"

    def __init__(self):
        self.cache = {f"forces__model1__{self.DS}": {"forces": np.zeros((2, 3, 3))}}
        _attach_env_facets(self)  # ADR 0020 sub-objects (env.data/datasets/models/remote)

    def getDataset(self, fp):
        return object() if fp == self.DS else None

    def getModel(self, key):
        return object() if key == "model1" else None

    def getAllModelKeys(self):
        return ["model1"]


class TestPredictionAttachment:
    """Regression: metric coloring must attach a prediction to the view, else
    the server falls back to element colors ('prediction.forces unavailable')."""

    def _loupe(self, label):
        import types
        return types.SimpleNamespace(
            settings=_FakeColorSettings(atomColorType=label, scenePredictionRef=""),
            env=_FakeColorEnv(),
            selectedDatasetKey=_FakeColorEnv.DS,
            _colorSourceByLabel={
                "Elements": "element",
                "Force Error": "metric:ffast.force_mae",
            },
            _colorSourceHooks={},
        )

    def test_metric_source_attaches_available_prediction(self):
        mod = _load_loupe_atoms()
        loupe = self._loupe("Force Error")
        mod._apply_coloring_selection(loupe)
        assert loupe.settings["scenePredictionRef"] == "model1"
        assert loupe.settings["atomColorSource"] == "metric:ffast.force_mae"

    def test_element_source_does_not_attach_prediction(self):
        mod = _load_loupe_atoms()
        loupe = self._loupe("Elements")
        mod._apply_coloring_selection(loupe)
        assert loupe.settings["scenePredictionRef"] == ""
        assert loupe.settings["atomColorSource"] == "element"


class _DS:
    isVariable = False
    _z = np.array([6, 1, 1, 1], dtype=np.int64)
    _R = np.tile(np.array([[0, 0, 0], [1, 0, 0], [0, 1, 0], [0, 0, 1]], dtype=float), (2, 1, 1))
    _F = np.zeros((2, 4, 3))  # reference forces = 0

    def getN(self): return 2
    def getCoordinates(self, idx): return self._R[idx]
    def getElements(self, idx=None): return self._z
    def getForces(self, indices=None): return self._F[indices]
    def getBondIndices(self, idx): return np.empty((0, 2), dtype=np.int64)


class _Pred:
    # predicted forces: atom 1 has a (3,4,0) error → |F|=5 (l2), mean|comp|=7/3 (l1)
    forces = np.stack(
        [np.array([[0, 0, 0], [3, 4, 0], [0, 0, 0], [0, 0, 0]], dtype=float)] * 2
    )


class TestServerMetricColoring:
    """Value-driven coloring through build_scene (ADR 0016)."""

    def _color_by(self, monkeypatch, extra_params):
        import ffast.visualization.color_values as cv
        from ffast.metrics.executor import InProcessExecutor
        # In-process executor → no worker subprocess in the test.
        monkeypatch.setattr(cv, "_executor", InProcessExecutor(reg))
        params = {"source": "metric:ffast.force_mae", "colormap": "viridis"}
        params.update(extra_params)
        state = VisualizationState(
            view_id="v", dataset_ref="fp", prediction_ref="m",
            structure_index=0, parameters={"ffast.atom_color": params},
        )
        scene = build_scene(state, lambda fp: _DS(), lambda d, m: _Pred())
        return scene.atoms.color_by

    def test_metric_coloring_produces_per_atom_values(self, monkeypatch):
        cb = self._color_by(monkeypatch, {})
        assert cb is not None
        assert len(cb.values) == 4
        # default norm is l2 → high-error atom = 5.0
        assert cb.values[1] == pytest.approx(5.0)
        assert cb.colormap == "viridis"

    def test_compute_parameter_changes_colors(self, monkeypatch):
        cb = self._color_by(monkeypatch, {"norm": "l1"})
        assert cb is not None
        # l1 norm → mean of |components| = (3+4+0)/3
        assert cb.values[1] == pytest.approx(7.0 / 3.0)

    def test_per_element_metric_broadcasts_onto_atoms(self, monkeypatch):
        """A per-element metric (N_elements) colors atoms by broadcasting each
        element's value onto its atoms. _DS is C,H,H,H."""
        import ffast.visualization.color_values as cv
        from ffast.metrics.executor import InProcessExecutor
        monkeypatch.setattr(cv, "_executor", InProcessExecutor(reg))
        state = VisualizationState(
            view_id="v", dataset_ref="fp", prediction_ref="m", structure_index=0,
            parameters={"ffast.atom_color": {
                "source": "metric:ffast.force_mae_per_element", "colormap": "viridis",
            }},
        )
        scene = build_scene(state, lambda fp: _DS(), lambda d, m: _Pred())
        cb = scene.atoms.color_by
        assert cb is not None
        assert len(cb.values) == 4                       # one value per atom
        assert cb.values[1] == pytest.approx(cb.values[2])   # both H atoms equal
        assert cb.values[0] != pytest.approx(cb.values[1])   # C differs from H
        assert cb.label == "Force Error (per element)"        # metric display name

    def test_accel_metric_resolves_via_ase_mass_fallback(self, monkeypatch):
        """Acceleration error needs atomic masses; _DS has no getMasses, so the
        server must derive them from the elements (ASE) instead of falling back
        to element colors."""
        import ffast.visualization.color_values as cv
        from ffast.metrics.executor import InProcessExecutor
        monkeypatch.setattr(cv, "_executor", InProcessExecutor(reg))
        state = VisualizationState(
            view_id="v", dataset_ref="fp", prediction_ref="m", structure_index=0,
            parameters={"ffast.atom_color": {
                "source": "metric:ffast.accel_mae_per_atom", "colormap": "viridis",
            }},
        )
        scene = build_scene(state, lambda fp: _DS(), lambda d, m: _Pred())
        cb = scene.atoms.color_by
        assert cb is not None
        assert len(cb.values) == 4
        assert cb.values[1] > 0.0   # atom 1 has the force/accel error
