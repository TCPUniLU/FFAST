from __future__ import annotations

import numpy as np
import pytest


class _Settings(dict):
    def get(self, key, default=None):
        return super().get(key, default)


class _MetricResult:
    def __init__(self, values):
        self.values = values


class _Dataset:
    isVariable = False
    fingerprint = "dataset-fp"

    _z = np.array([6, 1, 1], dtype=np.int64)
    _r = np.array(
        [
            [[0.0, 0.0, 0.0], [1.0, 0.0, 0.0], [0.0, 1.0, 0.0]],
            [[0.2, 0.0, 0.0], [1.2, 0.0, 0.0], [0.2, 1.0, 0.0]],
        ],
        dtype=np.float64,
    )
    # reference forces are zero; predicted = reference + diff = diff
    _f = np.zeros((2, 3, 3), dtype=np.float64)

    def getN(self):
        return 2

    def getNAtoms(self):
        return 3

    def getCoordinates(self, indices=None):
        if indices is None:
            return self._r
        return self._r[indices]

    def getElements(self, index=None):
        return self._z

    def getForces(self, indices=None):
        if indices is None:
            return self._f
        return self._f[indices]

    def getBondIndices(self, index):
        return np.array([[0, 1], [0, 2]], dtype=np.int64)


class _Model:
    def __init__(self, fingerprint, name):
        self.fingerprint = fingerprint
        self.name = name

    def getDisplayName(self):
        return self.name


from tests.ffast._env_facets import _attach_env_facets


class _Env:
    def __init__(self, dataset):
        self.dataset = dataset
        self.models = {
            "mace": _Model("mace", "MACE"),
            "other": _Model("other", "Other"),
        }
        # 4-part metric cache keys: metric_id__params_hash__model_fp__dataset_fp
        # predicted forces = 0.25, reference forces = 0 → diff = 0.25
        self.cache = {
            f"ffast.force_difference__nil__mace__{dataset.fingerprint}": _MetricResult(
                np.full((6, 3), 0.25, dtype=np.float64)  # 2 frames x 3 atoms flat
            ),
            f"ffast.force_difference__nil__other__different-dataset": _MetricResult(
                np.full((6, 3), 0.5, dtype=np.float64)
            ),
        }
        _attach_env_facets(self)  # ADR 0020 sub-objects

    def getDataset(self, fingerprint):
        return self.dataset if fingerprint == self.dataset.fingerprint else None

    def getModel(self, fingerprint):
        return self.models.get(fingerprint)

    def getAllModelKeys(self):
        return list(self.models)

    def make_metric_cache_key(self, metric_id, params, model, dataset):
        import hashlib, json
        model_fp = model.fingerprint if model is not None else "nil"
        dataset_fp = dataset.fingerprint if dataset is not None else "nil"
        params_hash = hashlib.md5(
            json.dumps(params, sort_keys=True).encode()
        ).hexdigest()[:8] if params else "nil"
        return f"{metric_id}__{params_hash}__{model_fp}__{dataset_fp}"

    def hasCacheKey(self, key, subChecks=True):
        return key in self.cache

    def getCacheByKey(self, key, subChecks=True):
        return self.cache.get(key)


def test_build_loupe_scene_snapshot_populates_selected_local_dataset():
    from ffast.renderers.vispy.local_scene import build_loupe_scene_snapshot

    ds = _Dataset()
    snapshot = build_loupe_scene_snapshot(
        view_id="loupe-view",
        dataset_ref=ds.fingerprint,
        structure_index=1,
        get_dataset=lambda fp: ds if fp == ds.fingerprint else None,
        settings=_Settings(),
    )

    assert snapshot.scene.view_id == "loupe-view"
    assert snapshot.scene.atoms is not None
    assert len(snapshot.scene.atoms.positions) == 3
    assert snapshot.scene.atoms.positions[0] == pytest.approx([0.2, 0.0, 0.0])
    assert snapshot.scene.bonds is not None


def test_available_prediction_refs_filters_models_by_dataset_forces():
    from ffast.renderers.vispy.local_scene import available_prediction_refs

    ds = _Dataset()
    assert available_prediction_refs(_Env(ds), ds.fingerprint) == ["mace"]


def test_build_loupe_scene_snapshot_prediction_ref_alone_does_not_show_forces():
    """Force vectors must only appear when showForceVectors=True + get_forces provided.
    Selecting a prediction (for metric coloring) must not produce force arrows."""
    from ffast.renderers.vispy.local_scene import (
        build_loupe_scene_snapshot,
        make_cache_prediction_resolver,
    )

    ds = _Dataset()
    env = _Env(ds)
    snapshot = build_loupe_scene_snapshot(
        view_id="loupe-view",
        dataset_ref=ds.fingerprint,
        structure_index=1,
        get_dataset=env.getDataset,
        settings=_Settings(),
        prediction_ref="mace",
        get_prediction=make_cache_prediction_resolver(env),
    )

    assert snapshot.scene.forces is None


def test_build_loupe_scene_snapshot_metric_coloring_uses_prediction_without_forces():
    from ffast.renderers.vispy.local_scene import (
        build_loupe_scene_snapshot,
        make_cache_prediction_resolver,
    )

    ds = _Dataset()
    env = _Env(ds)
    snapshot = build_loupe_scene_snapshot(
        view_id="loupe-view",
        dataset_ref=ds.fingerprint,
        structure_index=1,
        get_dataset=env.getDataset,
        settings=_Settings(atomColorSource="metric:ffast.force_mae"),
        prediction_ref="mace",
        get_prediction=make_cache_prediction_resolver(env),
    )

    assert snapshot.scene.atoms.color_by is not None
    assert snapshot.scene.atoms.color_by.label == "Force Error (per atom)"  # metric display name
    assert snapshot.scene.forces is None


def test_build_loupe_scene_snapshot_explicit_get_forces_shows_force_vectors():
    """Force vectors appear when showForceVectors=True and get_forces is supplied."""
    from ffast.renderers.vispy.local_scene import (
        build_loupe_scene_snapshot,
        make_force_resolver,
    )

    ds = _Dataset()
    env = _Env(ds)

    env2 = _Env(ds)
    get_forces = make_force_resolver(env2, model_key="mace")

    snapshot = build_loupe_scene_snapshot(
        view_id="loupe-view",
        dataset_ref=ds.fingerprint,
        structure_index=1,
        get_dataset=env2.getDataset,
        settings=_Settings(showForceVectors=True, forceVectorsNormalised=False),
        get_forces=get_forces,
    )

    assert snapshot.scene.forces is not None
    assert len(snapshot.scene.forces.vectors) == 3


def test_available_prediction_refs_handles_dataentity_cache():
    """Regression: live cache entries are DataEntity (``get(key=None)``, one
    positional arg), not dicts. available_prediction_refs must read them without
    crashing — otherwise the Prediction selector ends up empty and no prediction
    can be attached to the view (so metric coloring silently falls back to
    element colors)."""
    from ffast.renderers.vispy.local_scene import available_prediction_refs
    from client.dataType import DataEntity

    DS = "ds-fp"

    class _Env:
        def __init__(self):
            self.cache = {
                f"forces__m1__{DS}": DataEntity("forces", forces=np.zeros((2, 3, 3)))
            }
            _attach_env_facets(self)  # ADR 0020 sub-objects

        def getDataset(self, fp):
            return object() if fp == DS else None

        def getModel(self, key):
            return object() if key == "m1" else None

        def getAllModelKeys(self):
            return ["m1"]

    assert available_prediction_refs(_Env(), DS) == ["m1"]


def test_available_prediction_refs_decomposed_env_data_service():
    """Regression (ADR 0020): the cache and its key helpers live on ``env.data``
    (DataService), not on ``env`` itself — the old facade methods on ``env`` were
    deleted.  available_prediction_refs must read predictions through ``env.data``.

    The old code guarded every cache lookup with ``hasattr(env, "getCacheByKey")``
    / ``hasattr(env, "getCacheKey")`` (deleted facade names), so against a real
    decomposed env those were always False and the fallback hit a nonexistent
    ``env.cache`` → the lookup always returned None → the Loupe ATOMS Prediction
    selector stayed empty even with predictions cached (while 2D plots, which go
    through ``env.data`` directly, worked). The flat test doubles set
    ``env.data = env`` so they could never catch this.
    """
    import types
    from ffast.renderers.vispy.local_scene import available_prediction_refs
    from client.dataType import DataEntity

    DS = "ds-fp"

    class _DS:
        fingerprint = DS

    class _DataService:
        def __init__(self):
            self.cache = {
                f"forces__mace__{DS}": DataEntity("forces", forces=np.zeros((2, 3, 3)))
            }

        def getCacheKey(self, dtk, model=None, dataset=None):
            mfp = model if isinstance(model, str) else getattr(model, "fingerprint", "nil")
            dfp = dataset if isinstance(dataset, str) else getattr(dataset, "fingerprint", "nil")
            return f"{dtk}__{mfp}__{dfp}"

        def getCacheByKey(self, key, subChecks=True):
            return self.cache.get(key)

    class _DecomposedEnv:
        # Deliberately NO getCacheByKey / cache / getDataset / getModel on env —
        # those live only on the composed sub-objects, like the real Environment.
        def __init__(self):
            self.data = _DataService()
            self.datasets = types.SimpleNamespace(
                get=lambda fp: _DS() if fp == DS else None
            )
            self.models = types.SimpleNamespace(
                get=lambda k: _Model(k, k) if k in ("mace", "other") else None,
                all_keys=lambda: ["mace", "other"],
            )

    assert available_prediction_refs(_DecomposedEnv(), DS) == ["mace"]


def test_build_loupe_scene_snapshot_force_params_honor_length_and_normalised():
    """Local path must forward force params under the key build_scene reads
    (ffast.force_arrows / 'length_factor'). Regression: it wrote
    ffast.forces / 'length', which build_scene ignored, so the length and
    normalised controls had no effect on the rendered arrows."""
    from ffast.renderers.vispy.local_scene import (
        build_loupe_scene_snapshot,
        make_force_resolver,
    )

    ds = _Dataset()
    env = _Env(ds)
    get_forces = make_force_resolver(env, model_key="mace")  # predicted forces = 0.25

    def _max_arrow(length):
        snap = build_loupe_scene_snapshot(
            view_id="v",
            dataset_ref=ds.fingerprint,
            structure_index=0,
            get_dataset=env.getDataset,
            get_forces=get_forces,
            settings=_Settings(
                showForceVectors=True,
                forceVectorsNormalised=False,
                forceVectorsLength=length,
            ),
        )
        vecs = np.asarray(snap.scene.forces.vectors)
        return float(np.linalg.norm(vecs, axis=1).max())

    # normalised=False → arrow length scales linearly with forceVectorsLength.
    assert _max_arrow(200) == pytest.approx(_max_arrow(10) * 20, rel=1e-6)


def test_build_loupe_scene_snapshot_applies_local_adapter_settings():
    from ffast.renderers.vispy.local_scene import build_loupe_scene_snapshot

    ds = _Dataset()
    snapshot = build_loupe_scene_snapshot(
        view_id="loupe-view",
        dataset_ref=ds.fingerprint,
        structure_index=0,
        get_dataset=lambda fp: ds if fp == ds.fingerprint else None,
        settings=_Settings(
            showSceneLabels=True,
            sceneFilterIndices="0 H",
            sceneSelectIndices="0 2",
            atomColorSource="displacement",
            atomColorMap="plasma",
        ),
        picked_indices=[1],
    )

    assert snapshot.scene.labels is not None
    assert snapshot.scene.atoms is not None
    assert snapshot.scene.atoms.color_by is not None
    assert snapshot.scene.atoms.color_by.colormap == "plasma"
    assert [s.name for s in snapshot.scene.selections] == ["highlight", "picked"]
