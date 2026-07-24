"""Tests for ffast.visualization.scene_builder."""
import numpy as np
import pytest

from ffast.visualization.models import CameraState, VisualizationState
from ffast.visualization.scene import RenderScene
from ffast.visualization.scene_builder import build_scene, fill_patch_from_scene


# ── helpers ───────────────────────────────────────────────────────────────────

class _FakeDataset:
    """Minimal dataset stub: 3 frames, 4 atoms (C, H, H, H)."""

    isVariable = False

    _z = np.array([6, 1, 1, 1], dtype=np.int64)   # C H H H

    _R = np.array([
        [[0.0, 0.0, 0.0], [1.0, 0.0, 0.0], [0.0, 1.0, 0.0], [0.0, 0.0, 1.0]],
        [[0.1, 0.0, 0.0], [1.1, 0.0, 0.0], [0.1, 1.0, 0.0], [0.1, 0.0, 1.0]],
        [[0.2, 0.0, 0.0], [1.2, 0.0, 0.0], [0.2, 1.0, 0.0], [0.2, 0.0, 1.0]],
    ], dtype=np.float64)

    def getN(self):       return 3
    def getCoordinates(self, idx): return self._R[idx]
    def getElements(self, idx=None): return self._z

    def getBondMatrix(self, idx):
        R = self.getCoordinates(idx)
        from scipy.spatial import distance_matrix as dm
        d = dm(R, R)
        from ffast.chemistry import covalentBonds  # type: ignore
        z = self._z
        sizes = covalentBonds[z][:, z] * 1.2
        return d < sizes

    def getBondIndices(self, idx):
        import numpy as np
        adj = np.argwhere(self.getBondMatrix(idx))
        pairs = [(int(a), int(b)) for a, b in adj if a < b]
        if not pairs:
            return np.empty((0, 2), dtype=np.int64)
        return np.array(pairs, dtype=np.int64)


# ── build_scene ───────────────────────────────────────────────────────────────

class TestBuildSceneNoDataset:
    def test_returns_render_scene(self):
        state = VisualizationState(view_id="v1")
        scene = build_scene(state, get_dataset=lambda fp: None)
        assert isinstance(scene, RenderScene)

    def test_has_correct_view_id_and_version(self):
        state = VisualizationState(view_id="abc", version=3)
        scene = build_scene(state, get_dataset=lambda fp: None)
        assert scene.view_id == "abc"
        assert scene.version == 3

    def test_atoms_none_when_no_dataset(self):
        state = VisualizationState(view_id="v1")
        scene = build_scene(state, get_dataset=lambda fp: None)
        assert scene.atoms is None

    def test_camera_preserved(self):
        cam = CameraState(distance=25.0, fov=45.0)
        state = VisualizationState(view_id="v1", camera=cam)
        scene = build_scene(state, get_dataset=lambda fp: None)
        assert scene.camera.distance == 25.0
        assert scene.camera.fov == 45.0

    def test_dataset_ref_none_returns_empty_scene(self):
        state = VisualizationState(view_id="v1", dataset_ref=None)
        scene = build_scene(state, get_dataset=lambda fp: None)
        assert scene.atoms is None
        assert scene.bonds is None


class TestBuildSceneWithDataset:
    @pytest.fixture
    def ds(self):
        return _FakeDataset()

    def _state(self, **kw):
        return VisualizationState(view_id="v1", dataset_ref="fake_fp", **kw)

    def test_atoms_populated(self, ds):
        state = self._state()
        scene = build_scene(state, get_dataset=lambda fp: ds)
        assert scene.atoms is not None
        assert len(scene.atoms.positions) == 4

    def test_atom_positions_correct(self, ds):
        state = self._state(structure_index=0)
        scene = build_scene(state, get_dataset=lambda fp: ds)
        assert len(scene.atoms.positions) == 4
        assert scene.atoms.positions[0] == pytest.approx([0.0, 0.0, 0.0])

    def test_atom_positions_change_with_frame(self, ds):
        s0 = build_scene(self._state(structure_index=0), get_dataset=lambda fp: ds)
        s2 = build_scene(self._state(structure_index=2), get_dataset=lambda fp: ds)
        assert s0.atoms.positions[0][0] == pytest.approx(0.0)
        assert s2.atoms.positions[0][0] == pytest.approx(0.2)

    def test_atom_sizes_present(self, ds):
        state = self._state()
        scene = build_scene(state, get_dataset=lambda fp: ds)
        sizes = scene.atoms.sizes
        assert len(sizes) == 4
        assert all(s > 0 for s in sizes)

    def test_atom_colors_rgba(self, ds):
        state = self._state()
        scene = build_scene(state, get_dataset=lambda fp: ds)
        colors = scene.atoms.colors
        assert len(colors) == 4
        for rgba in colors:
            assert len(rgba) == 4
            assert all(0.0 <= c <= 1.0 for c in rgba)

    def test_bonds_populated(self, ds):
        state = self._state()
        scene = build_scene(state, get_dataset=lambda fp: ds)
        assert scene.bonds is not None
        assert len(scene.bonds.segments) > 0

    def test_bond_segments_even_count(self, ds):
        state = self._state()
        scene = build_scene(state, get_dataset=lambda fp: ds)
        assert len(scene.bonds.segments) % 2 == 0

    def test_structure_index_clamped_to_valid_range(self, ds):
        # An out-of-range index must clamp to the LAST valid frame, not just
        # "any" frame — compare against building the scene with that exact
        # last index (ds.getN() - 1) and require the resolved atoms to match.
        last_valid = ds.getN() - 1
        state = self._state(structure_index=999)
        scene = build_scene(state, get_dataset=lambda fp: ds)
        expected = build_scene(self._state(structure_index=last_valid), get_dataset=lambda fp: ds)
        assert scene.atoms is not None
        assert scene.atoms.positions == expected.atoms.positions

    def test_unknown_dataset_returns_empty(self):
        state = VisualizationState(view_id="v1", dataset_ref="missing")
        scene = build_scene(state, get_dataset=lambda fp: None)
        assert scene.atoms is None


# ── fill_patch_from_scene ─────────────────────────────────────────────────────

class TestFillPatchFromScene:
    def test_fills_atoms_when_in_changed(self):
        from ffast.visualization.scene import AtomScene, ScenePatch
        patch = ScenePatch(view_id="v1", from_version=0, to_version=1, changed={"atoms"})
        scene = RenderScene(
            view_id="v1", version=1,
            atoms=AtomScene(positions=[[0,0,0]], sizes=[0.5], colors=[[1,0,0,1]]),
        )
        fill_patch_from_scene(patch, scene)
        assert patch.atoms is not None
        assert patch.atoms.positions == [[0, 0, 0]]

    def test_does_not_fill_atoms_when_not_in_changed(self):
        from ffast.visualization.scene import ScenePatch
        patch = ScenePatch(view_id="v1", from_version=0, to_version=1, changed={"camera"})
        scene = RenderScene(view_id="v1", version=1)
        fill_patch_from_scene(patch, scene)
        assert patch.atoms is None

    def test_fills_camera(self):
        from ffast.visualization.scene import ScenePatch
        cam = CameraState(distance=50.0)
        patch = ScenePatch(view_id="v1", from_version=0, to_version=1, changed={"camera"})
        scene = RenderScene(view_id="v1", version=1, camera=cam)
        fill_patch_from_scene(patch, scene)
        assert patch.camera is not None
        assert patch.camera.distance == 50.0

    def test_fills_unit_cell_when_in_changed(self):
        from ffast.visualization.scene import ScenePatch, UnitCellScene
        segs = [[0, 0, 0], [1, 0, 0]] * 12   # 24 rows
        patch = ScenePatch(view_id="v1", from_version=0, to_version=1, changed={"unit_cell"})
        scene = RenderScene(view_id="v1", version=1, unit_cell=UnitCellScene(segments=segs))
        fill_patch_from_scene(patch, scene)
        assert patch.unit_cell is not None
        assert len(patch.unit_cell.segments) == 24


# ── unit cell in build_scene ──────────────────────────────────────────────────

class _FakeDatasetWithLattice(_FakeDataset):
    """Dataset stub that also exposes a cubic unit cell via getLattice()."""

    _lattice = np.eye(3, dtype=np.float64) * 5.0   # 5Å cubic cell

    def getLattice(self, idx):
        return self._lattice


class TestBuildSceneLabels:
    @pytest.fixture
    def ds(self):
        return _FakeDataset()

    def test_no_labels_by_default(self, ds):
        state = VisualizationState(view_id="v1", dataset_ref="fp")
        scene = build_scene(state, get_dataset=lambda fp: ds)
        assert scene.labels is None

    def test_labels_built_when_feature_enabled(self, ds):
        state = VisualizationState(view_id="v1", dataset_ref="fp", enabled_features=["labels"])
        scene = build_scene(state, get_dataset=lambda fp: ds)
        assert scene.labels is not None
        assert scene.labels.texts == ["0", "1", "2", "3"]
        assert len(scene.labels.positions) == 4

    def test_label_element_mode_via_parameter(self, ds):
        state = VisualizationState(
            view_id="v1",
            dataset_ref="fp",
            enabled_features=["labels"],
            parameters={"ffast.atom_labels": {"mode": "element"}},
        )
        scene = build_scene(state, get_dataset=lambda fp: ds)
        # _FakeDataset is C H H H
        assert scene.labels.texts == ["C", "H", "H", "H"]


class TestBuildSceneParameterDriven:
    @pytest.fixture
    def ds(self):
        return _FakeDataset()

    def test_atom_size_scale_parameter_applied(self, ds):
        base = build_scene(
            VisualizationState(view_id="v1", dataset_ref="fp"),
            get_dataset=lambda fp: ds,
        )
        scaled = build_scene(
            VisualizationState(
                view_id="v1", dataset_ref="fp",
                parameters={"ffast.atom_sizes": {"scale": 2.0}},
            ),
            get_dataset=lambda fp: ds,
        )
        assert scaled.atoms.sizes[0] == pytest.approx(base.atoms.sizes[0] * 2.0)


class TestBuildSceneUnitCell:
    def test_unit_cell_populated_when_dataset_has_lattice(self):
        state = VisualizationState(view_id="v1", dataset_ref="fp")
        ds = _FakeDatasetWithLattice()
        scene = build_scene(state, get_dataset=lambda fp: ds)
        assert scene.unit_cell is not None
        # unit_cell_edges returns 24 endpoint rows for 12 edges
        assert len(scene.unit_cell.segments) == 24

    def test_unit_cell_none_when_dataset_has_no_getLattice(self):
        state = VisualizationState(view_id="v1", dataset_ref="fp")
        ds = _FakeDataset()   # no getLattice method
        scene = build_scene(state, get_dataset=lambda fp: ds)
        assert scene.unit_cell is None

    def test_unit_cell_none_when_getLattice_returns_none(self):
        class _NullLatticeDs(_FakeDataset):
            def getLattice(self, idx):
                return None

        state = VisualizationState(view_id="v1", dataset_ref="fp")
        scene = build_scene(state, get_dataset=lambda fp: _NullLatticeDs())
        assert scene.unit_cell is None

    def test_unit_cell_none_when_getLattice_raises(self):
        class _ErrLatticeDs(_FakeDataset):
            def getLattice(self, idx):
                raise RuntimeError("no lattice")

        state = VisualizationState(view_id="v1", dataset_ref="fp")
        scene = build_scene(state, get_dataset=lambda fp: _ErrLatticeDs())
        assert scene.unit_cell is None


# ── prediction force vectors (server wiring) ──────────────────────────────────

from tests.ffast._env_facets import _attach_env_facets


class _FakeEnvWithPrediction:
    """Stub of the headless Environment exposing the prediction cache the way
    the real server does: forces keyed via the production CacheKey format
    (see ``ffast.cache.keys.CacheKey``), not a hardcoded string literal, so
    this test survives a cache-key format refactor as long as
    ServerSession.get_prediction's hit/miss behavior is unchanged."""

    DS_FP = "DSfp"
    MODEL_FP = "MODELfp"

    def __init__(self):
        ds = _FakeDataset()
        # forces: one (4, 3) array per frame, distinct per frame so a frame
        # change produces a different ForceScene.
        forces = np.stack([
            np.full((4, 3), 0.1),
            np.full((4, 3), 0.2),
            np.full((4, 3), 0.3),
        ])
        from ffast.cache.keys import CacheKey
        cache_key = CacheKey("forces", self.MODEL_FP, self.DS_FP).format()
        self.cache = {cache_key: {"forces": forces}}
        self._ds = ds
        _attach_env_facets(self)  # ADR 0020 sub-objects

    def getDataset(self, fp):
        return self._ds if fp == self.DS_FP else None


class TestPredictionForces:
    """Covers the wiring that was missing before predictions reached the web
    renderer: server._make_get_prediction → build_scene → ForceScene."""

    def _get_prediction(self, env):
        import asyncio
        from ffast.session.server_session import ServerSession
        return ServerSession(env, asyncio.Queue()).get_prediction

    def test_adapter_hit_returns_forces(self):
        env = _FakeEnvWithPrediction()
        pred = self._get_prediction(env)(env.DS_FP, env.MODEL_FP)
        assert pred is not None and pred.forces is not None

    def test_adapter_miss_returns_none(self):
        env = _FakeEnvWithPrediction()
        get_pred = self._get_prediction(env)
        assert get_pred(env.DS_FP, "no-such-model") is None
        assert get_pred(None, env.MODEL_FP) is None
        assert get_pred(env.DS_FP, None) is None

    def test_adapter_none_when_forces_entry_missing(self):
        env = _FakeEnvWithPrediction()
        from ffast.cache.keys import CacheKey
        cache_key = CacheKey("forces", env.MODEL_FP, env.DS_FP).format()
        env.cache[cache_key] = {}  # no "forces" key
        assert self._get_prediction(env)(env.DS_FP, env.MODEL_FP) is None

    def test_build_scene_populates_forces(self):
        env = _FakeEnvWithPrediction()
        # Force arrows require explicit opt-in via the "forces" feature flag.
        state = VisualizationState(
            view_id="v1", dataset_ref=env.DS_FP, prediction_ref=env.MODEL_FP,
            enabled_features=["forces"],
        )
        scene = build_scene(state, env.getDataset, self._get_prediction(env))
        assert scene.forces is not None
        assert len(scene.forces.vectors) == 4          # one per atom
        assert len(scene.forces.starts) == 4
        assert scene.forces.vectors[0] == pytest.approx([0.1, 0.1, 0.1])

    def test_no_forces_without_prediction_ref(self):
        env = _FakeEnvWithPrediction()
        state = VisualizationState(
            view_id="v1", dataset_ref=env.DS_FP, enabled_features=["forces"],
        )  # no pred
        scene = build_scene(state, env.getDataset, self._get_prediction(env))
        assert scene.forces is None

    def test_no_forces_without_feature_flag(self):
        # prediction_ref alone must NOT show force arrows (used for metric coloring).
        env = _FakeEnvWithPrediction()
        state = VisualizationState(
            view_id="v1", dataset_ref=env.DS_FP, prediction_ref=env.MODEL_FP,
            # enabled_features does NOT include "forces"
        )
        scene = build_scene(state, env.getDataset, self._get_prediction(env))
        assert scene.forces is None

    def test_forces_track_frame(self):
        env = _FakeEnvWithPrediction()
        state = VisualizationState(
            view_id="v1", dataset_ref=env.DS_FP,
            prediction_ref=env.MODEL_FP, structure_index=2,
            enabled_features=["forces"],
        )
        scene = build_scene(state, env.getDataset, self._get_prediction(env))
        assert scene.forces.vectors[0] == pytest.approx([0.3, 0.3, 0.3])

    def test_snapshot_carries_forces_like_open_view(self):
        # Mirrors _handle_open_view: view.snapshot(get_dataset, get_prediction).
        from ffast.visualization.view import VisualizationView
        env = _FakeEnvWithPrediction()
        view = VisualizationView("v1")
        view.state.dataset_ref = env.DS_FP
        view.state.prediction_ref = env.MODEL_FP
        view.state.enabled_features = ["forces"]
        snap = view.snapshot(
            get_dataset=env.getDataset, get_prediction=self._get_prediction(env)
        )
        assert snap.scene.forces is not None
        assert len(snap.scene.forces.vectors) == 4


class _GroundTruthDataset(_FakeDataset):
    """_FakeDataset plus ground-truth forces (one (4,3) array per frame)."""

    _F = np.stack([
        np.full((4, 3), 0.1),
        np.full((4, 3), 0.2),
        np.full((4, 3), 0.3),
    ])

    def getForces(self, indices=None):
        return self._F[indices]


class _GroundTruthEnv:
    """Headless-Environment stub with NO cached predictions — force arrows can
    only come from ground-truth dataset forces (the default UI source)."""

    DS_FP = "GTfp"

    def __init__(self):
        self._ds = _GroundTruthDataset()
        self.cache = {}
        _attach_env_facets(self)  # ADR 0020 sub-objects

    def getDataset(self, fp):
        return self._ds if fp == self.DS_FP else None


class _CollectingOutbound:
    """Stand-in for the server's outbound asyncio.Queue."""

    def __init__(self):
        self.sent = []

    def put_nowait(self, data):
        self.sent.append(data)

    async def put(self, data):
        self.sent.append(data)


class TestGroundTruthForceVectors:
    """Regression for "no force vectors shown when enabled" (default source =
    Ground Truth, forceVectorsModelKey=None).

    The server drove ``build_scene`` with ``get_dataset`` + ``get_prediction``
    but never a ``get_forces`` resolver, so the ground-truth branch
    (``prediction_ref is None``) produced no arrows. Predicted-force arrows kept
    working because they flow through ``get_prediction``. These tests exercise
    the real server seams (``_handle_view_command`` and ``_handle_open_view``),
    which is where the wiring was missing.
    """

    def _run_command(self, env, view, command_kwargs):
        import asyncio
        from ffast.session.server_session import ServerSession
        from ffast.protocol.rpc import unpack
        session = ServerSession(env, _CollectingOutbound())
        session.views = {view.state.view_id: view}
        asyncio.run(session.dispatch("VIEW_COMMAND", [], command_kwargs))
        patches = [unpack(d) for d in session.outbound.sent]
        scene_patches = [kw for ev, _, kw in patches if ev == "SCENE_PATCH"]
        assert scene_patches, "handler emitted no SCENE_PATCH"
        return scene_patches[-1]

    def test_toggle_forces_renders_ground_truth_arrows(self):
        from ffast.visualization.view import VisualizationView
        env = _GroundTruthEnv()
        view = VisualizationView("v1")
        view.state.dataset_ref = env.DS_FP   # no prediction_ref → ground truth
        patch = self._run_command(env, view, {
            "type": "TOGGLE_FEATURE", "view_id": "v1", "view_version": 0,
            "feature": "forces", "enabled": True,
        })
        assert "forces" in patch["changed"]
        assert patch["forces"] is not None
        assert len(patch["forces"]["vectors"]) == 4
        assert len(patch["forces"]["starts"]) == 4

    def test_explicit_ground_truth_prediction_ref_renders_arrows(self):
        # The real UI always sends SET_PARAMETER(force_arrows, prediction_ref,
        # forceVectorsModelKey); the default value is None (Ground Truth).
        from ffast.visualization.view import VisualizationView
        env = _GroundTruthEnv()
        view = VisualizationView("v1")
        view.state.dataset_ref = env.DS_FP
        view.state.enabled_features = ["forces"]
        view.state.parameters["ffast.force_arrows"] = {"prediction_ref": None}
        patch = self._run_command(env, view, {
            "type": "SET_PARAMETER", "view_id": "v1", "view_version": 0,
            "stage_id": "ffast.force_arrows", "parameter": "length_factor", "value": 10,
        })
        assert patch["forces"] is not None
        assert len(patch["forces"]["vectors"]) == 4

    def test_open_view_snapshot_carries_ground_truth_forces(self):
        import asyncio
        from ffast.session.server_session import ServerSession
        env = _GroundTruthEnv()
        from ffast.visualization.view import VisualizationView
        view = VisualizationView("v1")
        view.state.dataset_ref = env.DS_FP
        view.state.enabled_features = ["forces"]
        session = ServerSession(env, asyncio.Queue())
        snap = view.snapshot(
            get_dataset=env.getDataset,
            get_prediction=session.get_prediction,
            get_forces=session.get_forces,
        )
        assert snap.scene.forces is not None
        assert len(snap.scene.forces.vectors) == 4


# ── atom_align / no_unit_cell (features build_scene must honor) ───────────────

class TestBuildSceneAtomAlign:
    """Regression: server-side 3-atom alignment (ffast.atom_align). The client
    sends TOGGLE_FEATURE('atom_align') + SET_PARAMETER(ffast.atom_align, ...),
    but build_scene only honored kabsch_align, so the feature did nothing.
    _FakeDataset frame 1 is frame 0 translated by [0.1, 0, 0]."""

    def test_atom_align_returns_frame_to_reference(self):
        ds = _FakeDataset()
        state = VisualizationState(
            view_id="v1", dataset_ref="fp", structure_index=1,
            enabled_features=["atom_align"],
            parameters={"ffast.atom_align": {"atom_indices": [0, 1, 2], "reference_frame": 0}},
        )
        scene = build_scene(state, get_dataset=lambda fp: ds)
        # frame 1 aligned onto frame 0 → atom 0 back at the origin
        assert scene.atoms.positions[0] == pytest.approx([0.0, 0.0, 0.0], abs=1e-9)

    def test_atom_align_noop_without_three_indices(self):
        ds = _FakeDataset()
        state = VisualizationState(
            view_id="v1", dataset_ref="fp", structure_index=1,
            enabled_features=["atom_align"],
            parameters={"ffast.atom_align": {"atom_indices": [0, 1], "reference_frame": 0}},
        )
        scene = build_scene(state, get_dataset=lambda fp: ds)
        # invalid selection → untransformed frame 1 (atom 0 at x = 0.1)
        assert scene.atoms.positions[0] == pytest.approx([0.1, 0.0, 0.0], abs=1e-9)


class TestBuildSceneNoUnitCell:
    """Regression: the 'no_unit_cell' opt-out. The client sends
    TOGGLE_FEATURE('no_unit_cell') to hide the cell, but build_scene always
    drew it when the dataset had a lattice."""

    def test_no_unit_cell_feature_hides_cell(self):
        ds = _FakeDatasetWithLattice()
        state = VisualizationState(
            view_id="v1", dataset_ref="fp", enabled_features=["no_unit_cell"],
        )
        scene = build_scene(state, get_dataset=lambda fp: ds)
        assert scene.unit_cell is None

    def test_unit_cell_shown_without_optout(self):
        ds = _FakeDatasetWithLattice()
        state = VisualizationState(view_id="v1", dataset_ref="fp")
        scene = build_scene(state, get_dataset=lambda fp: ds)
        assert scene.unit_cell is not None


class TestBuildSceneFixedBonds:
    """Regression: 'Fixed' bond mode (loupeBonds). build_scene always used the
    dynamic distance-based getBondIndices and ignored the ffast.bonds parameter,
    so the Fixed bond selection never took effect. _FakeDataset has 3 dynamic
    C-H bonds (6 endpoint rows)."""

    def test_fixed_indices_override_dynamic_bonds(self):
        ds = _FakeDataset()
        state = VisualizationState(
            view_id="v1", dataset_ref="fp",
            parameters={"ffast.bonds": {"bond_type": "Fixed", "fixed_indices": [[1, 2]]}},
        )
        scene = build_scene(state, get_dataset=lambda fp: ds)
        assert scene.bonds is not None
        assert len(scene.bonds.segments) == 2   # one explicit bond → 2 endpoints

    def test_fixed_empty_falls_back_to_dynamic(self):
        ds = _FakeDataset()
        state = VisualizationState(
            view_id="v1", dataset_ref="fp",
            parameters={"ffast.bonds": {"bond_type": "Fixed", "fixed_indices": []}},
        )
        scene = build_scene(state, get_dataset=lambda fp: ds)
        # an empty Fixed set must NOT blank the view → dynamic bonds (3 x 2 = 6)
        assert scene.bonds is not None
        assert len(scene.bonds.segments) == 6

    def test_dynamic_mode_uses_distance_bonds(self):
        ds = _FakeDataset()
        state = VisualizationState(
            view_id="v1", dataset_ref="fp",
            parameters={"ffast.bonds": {"bond_type": "Dynamic"}},
        )
        scene = build_scene(state, get_dataset=lambda fp: ds)
        assert len(scene.bonds.segments) == 6


# ── atom filter (ADR 0014 gate 3) ─────────────────────────────────────────────

class TestBuildSceneAtomFilter:
    @pytest.fixture
    def ds(self):
        return _FakeDataset()

    def _filtered(self, ds, indices, **kw):
        state = VisualizationState(
            view_id="v1", dataset_ref="fp",
            parameters={"ffast.atom_filter": {"indices": indices, **kw}},
        )
        return build_scene(state, get_dataset=lambda fp: ds)

    def test_empty_indices_keeps_all(self, ds):
        scene = self._filtered(ds, [])
        assert len(scene.atoms.positions) == 4
        assert scene.atoms.atom_ids is None  # identity when unfiltered

    def test_filter_subsets_atoms(self, ds):
        scene = self._filtered(ds, [0, 2])
        assert len(scene.atoms.positions) == 2
        assert scene.atoms.positions[0] == pytest.approx([0.0, 0.0, 0.0])  # atom 0
        assert scene.atoms.positions[1] == pytest.approx([0.0, 1.0, 0.0])  # atom 2

    def test_filter_sets_atom_ids_to_kept_originals(self, ds):
        scene = self._filtered(ds, [0, 2])
        assert scene.atoms.atom_ids == [0, 2]

    def test_invert_hides_listed_atoms(self, ds):
        scene = self._filtered(ds, [0], invert=True)
        assert len(scene.atoms.positions) == 3
        assert scene.atoms.atom_ids == [1, 2, 3]

    def test_bonds_dropped_and_remapped(self, ds):
        # Keep only C (0) and one H (1): the single surviving bond 0-1 remaps to
        # compact indices 0-1, so every segment endpoint is within [0, 2) atoms.
        scene = self._filtered(ds, [0, 1])
        assert scene.atoms is not None and len(scene.atoms.positions) == 2
        if scene.bonds is not None:
            segs = np.asarray(scene.bonds.segments)
            # every segment endpoint must coincide with a kept atom position
            kept = np.asarray(scene.atoms.positions)
            for s in segs:
                assert np.min(np.linalg.norm(kept - s, axis=1)) == pytest.approx(0.0)

    def test_labels_subset_keeps_original_index_text(self, ds):
        state = VisualizationState(
            view_id="v1", dataset_ref="fp",
            enabled_features=["labels"],
            parameters={"ffast.atom_filter": {"indices": [0, 2]}},
        )
        scene = build_scene(state, get_dataset=lambda fp: ds)
        assert scene.labels.texts == ["0", "2"]
        assert len(scene.labels.positions) == 2


# ── Kabsch alignment feature (ADR 0014 gate 2) ────────────────────────────────

class TestBuildSceneKabsch:
    @pytest.fixture
    def ds(self):
        return _FakeDataset()

    def test_frame2_aligns_onto_frame0(self, ds):
        # _FakeDataset frames are frame0 translated by (0.1*i, 0, 0), so aligning
        # frame 2 onto frame 0 is a pure -0.2x translation → frame0 positions.
        state = VisualizationState(
            view_id="v1", dataset_ref="fp",
            enabled_features=["kabsch_align"], structure_index=2,
        )
        scene = build_scene(state, get_dataset=lambda fp: ds)
        assert scene.atoms.positions[0] == pytest.approx([0.0, 0.0, 0.0], abs=1e-9)
        assert scene.atoms.positions[1] == pytest.approx([1.0, 0.0, 0.0], abs=1e-9)

    def test_no_align_without_feature(self, ds):
        state = VisualizationState(view_id="v1", dataset_ref="fp", structure_index=2)
        scene = build_scene(state, get_dataset=lambda fp: ds)
        assert scene.atoms.positions[0] == pytest.approx([0.2, 0.0, 0.0])


# ── selection overlays (ADR 0014 / 0015) ──────────────────────────────────────

class TestBuildSceneSelectionOverlays:
    @pytest.fixture
    def ds(self):
        return _FakeDataset()

    def _sel(self, indices):
        from ffast.visualization.models import ScientificSelection, SelectionScope
        return {"picked": ScientificSelection(
            name="picked", scope=SelectionScope.CURRENT_STRUCTURE, indices=indices,
        )}

    def test_overlay_emitted_from_state_selection(self, ds):
        state = VisualizationState(
            view_id="v1", dataset_ref="fp", selections=self._sel([1, 2]),
        )
        scene = build_scene(state, get_dataset=lambda fp: ds)
        assert len(scene.selections) == 1
        assert scene.selections[0].name == "picked"
        assert scene.selections[0].atom_indices == [1, 2]

    def test_no_overlay_without_selection(self, ds):
        state = VisualizationState(view_id="v1", dataset_ref="fp")
        scene = build_scene(state, get_dataset=lambda fp: ds)
        assert scene.selections == []

    def test_overlay_indices_remapped_under_filter(self, ds):
        # Keep atoms [0,2,3]; selection {2,3} → compact indices {1,2}.
        state = VisualizationState(
            view_id="v1", dataset_ref="fp",
            selections=self._sel([2, 3]),
            parameters={"ffast.atom_filter": {"indices": [0, 2, 3]}},
        )
        scene = build_scene(state, get_dataset=lambda fp: ds)
        assert scene.selections[0].atom_indices == [1, 2]

    def test_overlay_drops_filtered_out_atoms(self, ds):
        # Keep only [0,1]; selection {2} is filtered out → empty overlay.
        state = VisualizationState(
            view_id="v1", dataset_ref="fp",
            selections=self._sel([2]),
            parameters={"ffast.atom_filter": {"indices": [0, 1]}},
        )
        scene = build_scene(state, get_dataset=lambda fp: ds)
        assert scene.selections[0].atom_indices == []


# ── value-driven atom coloring (ADR 0016) ─────────────────────────────────────

class _FakeColorDataset(_FakeDataset):
    """Adds whole-trajectory getCoordinates() and reference getForces()."""

    def getCoordinates(self, indices=None):
        return self._R if indices is None else self._R[indices]

    def getForces(self, indices=None):
        # reference forces, distinct per frame
        F = np.stack([
            np.zeros((4, 3)),
            np.ones((4, 3)),
            np.full((4, 3), 2.0),
        ])
        return F if indices is None else F[indices]

    def getElementsName(self):
        return ["C", "H", "H", "H"]


class _Pred:
    def __init__(self, forces):
        self.forces = forces


class TestBuildSceneColorBy:
    @pytest.fixture
    def ds(self):
        return _FakeColorDataset()

    def test_no_color_by_for_element_source(self, ds):
        state = VisualizationState(view_id="v1", dataset_ref="fp")
        scene = build_scene(state, get_dataset=lambda fp: ds)
        assert scene.atoms.color_by is None

    def test_displacement_source_populates_color_by(self, ds):
        state = VisualizationState(
            view_id="v1", dataset_ref="fp",
            parameters={"ffast.atom_color": {"source": "displacement"}},
        )
        scene = build_scene(state, get_dataset=lambda fp: ds)
        assert scene.atoms.color_by is not None
        assert len(scene.atoms.color_by.values) == 4
        assert scene.atoms.color_by.label == "displacement"

    def test_colormap_param_carried_in_descriptor(self, ds):
        state = VisualizationState(
            view_id="v1", dataset_ref="fp",
            parameters={"ffast.atom_color": {"source": "displacement", "colormap": "plasma"}},
        )
        scene = build_scene(state, get_dataset=lambda fp: ds)
        assert scene.atoms.color_by.colormap == "plasma"

    def test_explicit_vmin_vmax_override_auto(self, ds):
        state = VisualizationState(
            view_id="v1", dataset_ref="fp",
            parameters={"ffast.atom_color": {
                "source": "displacement", "vmin": 0.0, "vmax": 10.0,
            }},
        )
        scene = build_scene(state, get_dataset=lambda fp: ds)
        assert scene.atoms.color_by.vmin == 0.0
        assert scene.atoms.color_by.vmax == 10.0

    def test_metric_source_force_mae(self, ds):
        from ffast.metrics.executor import InProcessExecutor
        from ffast.metrics.registry import default_registry

        pred = np.stack([np.full((4, 3), 0.5)] * 3)  # predicted forces
        state = VisualizationState(
            view_id="v1", dataset_ref="DS", prediction_ref="M", structure_index=1,
            parameters={"ffast.atom_color": {"source": "metric:ffast.force_mae"}},
        )
        scene = build_scene(
            state, get_dataset=lambda fp: ds,
            get_prediction=lambda dfp, mfp: _Pred(pred),
            executor=InProcessExecutor(default_registry),
        )
        assert scene.atoms.color_by is not None
        assert scene.atoms.color_by.label == "Force Error (per atom)"  # metric display name
        assert len(scene.atoms.color_by.values) == 4

    def test_metric_source_falls_back_without_prediction(self, ds, caplog):
        # No prediction_ref → prediction.forces unresolvable → graceful None.
        state = VisualizationState(
            view_id="v1", dataset_ref="DS",
            parameters={"ffast.atom_color": {"source": "metric:ffast.force_mae"}},
        )
        scene = build_scene(state, get_dataset=lambda fp: ds)
        assert scene.atoms.color_by is None
        assert "color source 'metric:ffast.force_mae' failed" not in caplog.text

    def test_unknown_metric_falls_back(self, ds):
        state = VisualizationState(
            view_id="v1", dataset_ref="fp",
            parameters={"ffast.atom_color": {"source": "metric:no.such.metric"}},
        )
        scene = build_scene(state, get_dataset=lambda fp: ds)
        assert scene.atoms.color_by is None

    def test_color_values_subset_by_filter(self, ds):
        state = VisualizationState(
            view_id="v1", dataset_ref="fp",
            parameters={
                "ffast.atom_color": {"source": "displacement"},
                "ffast.atom_filter": {"indices": [0, 2]},
            },
        )
        scene = build_scene(state, get_dataset=lambda fp: ds)
        assert len(scene.atoms.positions) == 2
        assert len(scene.atoms.color_by.values) == 2  # subset like atoms


# ── element-syntax filter resolution (ADR 0014 gate 3) ────────────────────────

class TestResolveFilterIndices:
    # current-frame atomic numbers: C H H O
    _Z = np.array([6, 1, 1, 8], dtype=np.int64)

    def _resolve(self, raw):
        from ffast.visualization.scene_builder import _resolve_filter_indices
        return _resolve_filter_indices(raw, self._Z)

    def test_empty(self):
        assert self._resolve([]) == []

    def test_integer_indices(self):
        assert self._resolve([0, 2]) == [0, 2]

    def test_element_include(self):
        assert self._resolve(["H"]) == [1, 2]

    def test_element_exclude_keeps_rest(self):
        assert self._resolve(["-H"]) == [0, 3]

    def test_multiple_elements(self):
        assert self._resolve(["C", "O"]) == [0, 3]

    def test_index_exclude_from_all(self):
        assert self._resolve(["-1"]) == [0, 2, 3]

    def test_mixed_include_and_exclude(self):
        # include all H (1,2), exclude index 2 → keep [1]
        assert self._resolve(["H", "-2"]) == [1]

    def test_uses_current_frame_z_not_flat(self):
        from ffast.visualization.scene_builder import _resolve_filter_indices
        # A frame whose composition differs from any "flat" structure: O H C.
        z_frame = np.array([8, 1, 6], dtype=np.int64)
        assert _resolve_filter_indices(["C"], z_frame) == [2]
        assert _resolve_filter_indices(["-O"], z_frame) == [1, 2]


class TestAtomPipelineFallback:
    def test_atom_pipeline_exception_falls_back_to_neutral_styling(self, monkeypatch):
        # If the stage pipeline itself throws (e.g. element radii/colors config
        # unavailable), atoms must still render: positions from the raw
        # transforms, neutral gray color (0.7) and size 0.5, downstream stages
        # skipped — never a blank view.
        import ffast.visualization.pipeline as pipeline_mod

        def _boom(*a, **k):
            raise RuntimeError("pipeline down")

        # build_scene imports `execute` function-locally from the pipeline
        # module, so patch it at the source.
        monkeypatch.setattr(pipeline_mod, "execute", _boom)
        state = VisualizationState(view_id="v1", dataset_ref="fp", structure_index=0)
        scene = build_scene(state, get_dataset=lambda fp: _FakeDataset())

        assert scene.atoms is not None
        assert len(scene.atoms.positions) == 4          # positions still present
        assert scene.atoms.sizes == [0.5] * 4
        assert all(c == [0.7, 0.7, 0.7, 1.0] for c in scene.atoms.colors)


class TestForceSceneFilterRemap:
    """_build_force_scene's filter_enabled/atom_indices remap branch: force
    arrows for an explicit atom subset under an active atom filter."""

    def _forces_4(self):
        return np.array([[1.0, 0, 0], [2.0, 0, 0], [3.0, 0, 0], [4.0, 0, 0]])

    def _keep_1_and_3(self):
        keep = np.array([False, True, False, True])
        old_to_new = -np.ones(4, dtype=int)
        old_to_new[keep] = np.arange(2)  # [-1, 0, -1, 1]
        return keep, old_to_new

    def test_filter_remaps_forces_onto_kept_subset(self):
        from ffast.visualization.scene_builder import _build_force_scene
        keep, old_to_new = self._keep_1_and_3()
        positions = np.array([[10.0, 0, 0], [20.0, 0, 0]])  # post-filter compact
        scene = _build_force_scene(
            positions=positions,
            forces=self._forces_4(),                # full 4-atom forces
            keep=keep,
            old_to_new=old_to_new,
            transforms=[],
            params={"filter_enabled": True, "atom_indices": [3],
                    "normalised": False, "length_factor": 500},
        )
        assert scene is not None
        # scientific idx 3 → compact idx 1 → position [20,0,0]; its force (the
        # 4th, post-keep index 1) is [4,0,0], scaled by length/500 == 1.0.
        assert scene.starts == [[20.0, 0.0, 0.0]]
        assert scene.vectors == [[4.0, 0.0, 0.0]]

    def test_filter_with_only_excluded_indices_returns_none(self):
        from ffast.visualization.scene_builder import _build_force_scene
        keep, old_to_new = self._keep_1_and_3()
        scene = _build_force_scene(
            positions=np.array([[10.0, 0, 0], [20.0, 0, 0]]),
            forces=self._forces_4(),
            keep=keep,
            old_to_new=old_to_new,
            transforms=[],
            # scientific idx 0 is filtered out (old_to_new[0] == -1) → compact empty
            params={"filter_enabled": True, "atom_indices": [0]},
        )
        assert scene is None


class TestBuildSceneElementFilter:
    def test_filter_by_element_symbol(self):
        ds = _FakeColorDataset()  # C H H H
        state = VisualizationState(
            view_id="v1", dataset_ref="fp",
            parameters={"ffast.atom_filter": {"indices": ["H"]}},
        )
        scene = build_scene(state, get_dataset=lambda fp: ds)
        assert len(scene.atoms.positions) == 3       # the three H atoms
        assert scene.atoms.atom_ids == [1, 2, 3]
