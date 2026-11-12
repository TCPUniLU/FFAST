"""Prediction fields have one lifecycle: a load replaces, a delete releases.

Architecture review (Nov 2026), candidate 02: deleting a dataset or a model
left its prediction fields in memory, and a reload whose source lacked a field
still resolved the old values under the same fingerprints.
"""

import numpy as np
import pytest

from ffast.core.environment import Environment
from ffast.core.loading_coordinator import LoadingCoordinator
from ffast.core.prediction_fields import PredictionFieldStore
from ffast.metrics.input_resolver import InputResolver


class _Dataset:
    isSubDataset = False
    active = True
    parent = None

    def __init__(self, fingerprint, n=2):
        self.fingerprint = fingerprint
        self._n = n

    def getN(self):
        return self._n

    def getName(self):
        return f"{self.fingerprint}.extxyz"

    def onDelete(self):
        pass


class _Model:
    isGhost = True

    def __init__(self, fingerprint):
        self.fingerprint = fingerprint

    def onDelete(self):
        pass


class _Source:
    """Prediction loader double: just the field surface extraction reads."""

    def __init__(self, info=None):
        self._info = info or {}

    def availableFieldKeys(self):
        return sorted(self._info), []

    def getFrameField(self, key, indices=None):
        return self._info.get(key)

    def getAtomField(self, key, indices=None):
        return None


@pytest.fixture
def no_declared_fields(monkeypatch):
    monkeypatch.setattr(
        "ffast.metrics.fields.declared_field_keys",
        lambda side, registry=None: {"info": set(), "atoms": set()},
    )


@pytest.fixture
def env():
    return Environment()


def _load(env, model_fp, dataset, info):
    LoadingCoordinator(env)._extractPredictionFields(_Source(info), model_fp, dataset)


def _resolve(env, model_fp, dataset, key="dipole"):
    return InputResolver(env=env.data).resolve(
        f"prediction.info.{key}", model=_Model(model_fp), dataset=dataset)


def test_the_environment_shares_one_store_with_loads_and_deletes(env):
    assert isinstance(env.data.predictionFields, PredictionFieldStore)
    assert env.datasets._predictionFields is env.data.predictionFields
    assert env.models._predictionFields is env.data.predictionFields


def test_deleting_a_dataset_releases_its_prediction_fields(env, no_declared_fields):
    ds = _Dataset("ds")
    env.datasets.add(ds)
    _load(env, "m", ds, {"dipole": np.array([0.1, 0.2])})
    assert ("m", "ds") in env.data.predictionFields

    env.datasets.delete("ds")

    assert env.data.predictionFields == {}


def test_deleting_a_model_releases_its_prediction_fields(env, no_declared_fields):
    ds = _Dataset("ds")
    env.datasets.add(ds)
    env.models.add(_Model("m"))
    _load(env, "m", ds, {"dipole": np.array([0.1, 0.2])})
    _load(env, "other", ds, {"dipole": np.array([0.5, 0.6])})

    env.models.delete("m")

    assert list(env.data.predictionFields) == [("other", "ds")]


def test_a_reload_without_the_field_resolves_to_nothing(env, no_declared_fields):
    """Same fingerprints, fewer fields: the old value must not come back."""
    ds = _Dataset("ds")
    env.datasets.add(ds)
    _load(env, "m", ds, {"dipole": np.array([0.1, 0.2])})
    np.testing.assert_allclose(_resolve(env, "m", ds), [0.1, 0.2])

    _load(env, "m", ds, {})

    assert _resolve(env, "m", ds) is None
    assert env.data.predictionFields == {}


def test_a_reload_with_other_fields_drops_the_ones_it_lacks(env, no_declared_fields):
    ds = _Dataset("ds")
    env.datasets.add(ds)
    _load(env, "m", ds, {"dipole": np.array([0.1, 0.2])})

    _load(env, "m", ds, {"charge": np.array([1.0, 2.0])})

    assert _resolve(env, "m", ds) is None
    np.testing.assert_allclose(_resolve(env, "m", ds, "charge"), [1.0, 2.0])


def test_a_reload_whose_keys_cannot_be_read_drops_the_old_fields(env, no_declared_fields):
    class _Broken(_Source):
        def availableFieldKeys(self):
            raise OSError("unreadable")

    ds = _Dataset("ds")
    env.datasets.add(ds)
    _load(env, "m", ds, {"dipole": np.array([0.1, 0.2])})

    LoadingCoordinator(env)._extractPredictionFields(_Broken(), "m", ds)

    assert env.data.predictionFields == {}


def test_the_store_replaces_and_drops():
    store = PredictionFieldStore()
    store.replace("m", "ds", {"info": {"a": 1}, "atoms": {}})
    store.replace("m", "ds2", {"info": {}, "atoms": {"b": 2}})
    store.replace("m2", "ds", {"info": {"a": 3}, "atoms": {}})

    store.replace("m", "ds", {"info": {}, "atoms": {}})     # empty → removed
    assert set(store) == {("m", "ds2"), ("m2", "ds")}

    store.drop_object("ds")
    assert set(store) == {("m", "ds2")}
    store.drop_object("m")
    assert store == {}
