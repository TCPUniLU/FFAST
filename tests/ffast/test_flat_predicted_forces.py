"""Predicted forces are stored as one FlatFrames (ADR 0080)."""
from __future__ import annotations

import asyncio
from types import SimpleNamespace

import numpy as np
import pytest

from ffast.core.data_types import ForcesPredictionData
from ffast.core.flat_frames import FlatFrames
from ffast.session.persistence import SessionPersistence
from ffast.session.server_session import ServerSession

from tests.ffast.test_element_subset import (
    CARBON, _carbons, _element_subset, _uniform_parent, _variable_parent,
)


class _Catalog:
    def snapshot(self):
        return {}

    def load(self, objects):
        pass


def _forces_type():
    return ForcesPredictionData(None)


def _ragged_frames():
    return [np.full((n, 3), float(n)) + np.arange(n)[:, None] for n in (3, 1, 2, 4)]


def test_new_data_entity_flattens_lists_and_arrays_and_refuses_a_plain_2d_array():
    dt = _forces_type()
    ragged = _ragged_frames()
    ff = dt.newDataEntity(forces=ragged).get("forces")
    assert isinstance(ff, FlatFrames)
    assert ff.offsets.tolist() == [0, 3, 4, 6, 10]
    for got, want in zip(ff, ragged):
        np.testing.assert_array_equal(got, want)

    arr = np.arange(24, dtype=float).reshape(4, 2, 3)
    ff = dt.newDataEntity(forces=arr).get("forces")
    assert isinstance(ff, FlatFrames) and ff.offsets.tolist() == [0, 2, 4, 6, 8]

    with pytest.raises(ValueError):
        dt.newDataEntity(forces=np.zeros((6, 3)))


@pytest.mark.parametrize("ragged", [True, False])
def test_a_frame_subset_holds_exactly_its_frames(ragged):
    frames = _ragged_frames() if ragged else [np.full((2, 3), float(i)) for i in range(4)]
    entity = _forces_type().newDataEntity(forces=frames)
    sub = entity.getSubEntity([3, 1]).get("forces")
    assert isinstance(sub, FlatFrames) and len(sub) == 2
    np.testing.assert_array_equal(sub[0], frames[3])
    np.testing.assert_array_equal(sub[1], frames[1])


def test_an_element_view_keeps_each_frames_carbons():
    parent = _variable_parent()
    view = _element_subset(parent, [CARBON])
    predicted = [np.asarray(parent.getForces(i)) + 1.0 for i in range(parent.getN())]
    kept = _forces_type().newDataEntity(forces=predicted).getAtomFilteredEntity(view).get("forces")
    assert isinstance(kept, FlatFrames)
    for i in range(parent.getN()):
        np.testing.assert_allclose(kept[i], predicted[i][_carbons(parent, i)])


def test_an_atom_view_of_same_size_frames_keeps_the_same_atoms():
    predicted = np.arange(4 * 5 * 3, dtype=float).reshape(4, 5, 3)
    entity = _forces_type().newDataEntity(forces=predicted)
    kept = entity.getAtomFilteredEntity([1, 3]).get("forces")
    assert kept.offsets.tolist() == [0, 2, 4, 6, 8]
    np.testing.assert_array_equal(kept[2], predicted[2][[1, 3]])


def test_a_metric_input_is_the_stored_array_itself():
    from ffast.metrics.input_resolver import InputResolver

    entity = _forces_type().newDataEntity(forces=_ragged_frames())
    env = SimpleNamespace(getData=lambda *a, **k: entity)
    got = InputResolver(env).resolve("prediction.forces", model=object(), dataset=object())
    assert np.shares_memory(got, entity.get("forces").values)


def test_a_session_saves_and_loads_flat_forces_and_still_loads_the_old_layout(tmp_path):
    dt = _forces_type()
    ragged = _ragged_frames()
    cache = {"forces__m__d": dt.newDataEntity(forces=ragged)}
    env = SimpleNamespace(
        cache=cache, objects=_Catalog(), datasetTypes={},
        datasets=SimpleNamespace(all=lambda *a, **k: []),
        models=SimpleNamespace(all=lambda *a, **k: []),
    )
    saved = tmp_path / "s"
    SessionPersistence(env).save(str(saved))

    loaded = {}
    env2 = SimpleNamespace(
        cache=loaded, objects=_Catalog(), datasetTypes={},
        data=SimpleNamespace(getDataType=lambda key: dt),
        eventPush=lambda *a, **k: None, lookForGhosts=lambda: None)
    SessionPersistence(env2).load(str(saved))
    ff = loaded["forces__m__d"].get("forces")
    assert isinstance(ff, FlatFrames)
    np.testing.assert_array_equal(ff.values, cache["forces__m__d"].get("forces").values)
    assert ff.offsets.tolist() == [0, 3, 4, 6, 10]

    # An npz written before ADR 0080: an object array of per-frame arrays.
    old = tmp_path / "old"
    (old / "cache").mkdir(parents=True)
    obj = np.empty(len(ragged), dtype=object)
    for i, f in enumerate(ragged):
        obj[i] = f
    np.savez_compressed(old / "cache" / "forces__m__d", entityDataTypeKey="forces",
                        cacheKey="forces__m__d", forces=obj)
    (old / "info.json").write_text('{"objects": {}}')
    loaded.clear()
    SessionPersistence(env2).load(str(old))
    ff = loaded["forces__m__d"].get("forces")
    assert isinstance(ff, FlatFrames) and ff.offsets.tolist() == [0, 3, 4, 6, 10]


class _FakeParent:
    isSubDataset = False
    isAtomFiltered = False

    def __init__(self, fp):
        self.fingerprint = fp


class _FakeSubset:
    isSubDataset = True
    isAtomFiltered = False

    def __init__(self, parent, frames):
        self.parent = parent
        self.frames = frames


def test_the_scene_gets_a_subsets_frames_of_the_parents_flat_forces():
    ragged = _ragged_frames()
    entity = _forces_type().newDataEntity(forces=ragged)
    parent = _FakeParent("ds1")
    env = SimpleNamespace(
        datasets=SimpleNamespace(get=lambda fp: {"ds1": parent, "sub": _FakeSubset(parent, [3, 1])}.get(fp)),
        cache=SimpleNamespace(get=lambda key: entity if key == "forces__m1__ds1" else None))
    view = ServerSession(env, asyncio.Queue()).get_prediction("sub", "m1")
    np.testing.assert_array_equal(view.forces[0], ragged[3])
    np.testing.assert_array_equal(view.forces[1], ragged[1])
