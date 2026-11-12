"""Deleting a dataset deletes the predictions read from a file for it alone.

A prediction file's numbers are stored per (prediction, dataset) and go when
the dataset goes; they cannot be moved to another dataset. A prediction left
with no numbers for any dataset used to stay in the list, greyed out, with
nothing to show. A model that computes predictions stays: it can predict any
dataset.
"""

import numpy as np
import pytest

from ffast.cache import CacheKey
from ffast.core.environment import Environment


class _Dataset:
    isSubDataset = False
    active = True
    parent = None

    def __init__(self, fingerprint, parent=None):
        self.fingerprint = fingerprint
        if parent is not None:
            self.parent = parent
            self.isSubDataset = True

    def getN(self):
        return 2

    def getName(self):
        return self.fingerprint

    def onDelete(self):
        pass


class _Model:
    def __init__(self, fingerprint, ghost):
        self.fingerprint = fingerprint
        self.isGhost = ghost

    def onDelete(self):
        pass


def _numbers(env, model, dataset):
    for dtype in ("energy", "forces"):
        env.cache[CacheKey(dtype, model, dataset).format()] = np.zeros(2)


@pytest.fixture
def env():
    e = Environment()
    parent = _Dataset("ds")
    e.datasets.add(parent)
    e.datasets.add(_Dataset("sub", parent=parent))
    e.datasets.add(_Dataset("other"))
    for fp, ghost in [("pred_ds", True), ("pred_both", True), ("pred_other", True),
                      ("live_model", False)]:
        e.models.add(_Model(fp, ghost))
    _numbers(e, "pred_ds", "ds")
    _numbers(e, "pred_both", "ds")
    _numbers(e, "pred_both", "other")
    _numbers(e, "pred_other", "other")
    _numbers(e, "live_model", "ds")
    return e


def test_a_prediction_only_for_the_deleted_dataset_goes_with_it(env):
    env.deleteObject("ds")

    assert "pred_ds" not in env.models


def test_a_prediction_with_numbers_for_another_dataset_stays(env):
    env.deleteObject("ds")

    assert "pred_both" in env.models
    assert "pred_other" in env.models


def test_a_model_that_computes_predictions_stays(env):
    env.deleteObject("ds")

    assert "live_model" in env.models


def test_deleting_a_subset_keeps_the_parents_predictions(env):
    env.deleteObject("sub")

    assert "pred_ds" in env.models


def test_the_delete_lists_the_predictions_it_takes(env):
    assert sorted(env.deletedWith("ds")) == ["ds", "pred_ds", "sub"]


def test_each_deleted_prediction_is_announced(env):
    env.eventSubscribe("MODEL_DELETED", lambda *_: None)

    env.deleteObject("ds")

    deleted = [args[0] for event, *_, args, _kw in env.eventQueue if event == "MODEL_DELETED"]
    assert deleted == ["pred_ds"]
