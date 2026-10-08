"""Deleting a dataset deletes everything cut from it.

A subset, a frozen subset and an atom subset all read their frames through
their parent (CONTEXT.md, Dataset): without it they mean nothing, and keeping
one alive kept the deleted parent in memory. Deleting a dataset now deletes
them too, a subset of a subset included, each announced as deleted on its
own so every window drops it; datasets cut from something else stay.

Exercised through the real ``DatasetRegistry`` with tiny fakes for the
datasets, cache and event bus.
"""

from types import SimpleNamespace

from ffast.cache.store import DataCache
from ffast.core.dataset_registry import DatasetRegistry
from ffast.core.object_catalog import ObjectCatalog


def _registry():
    pushed = []
    events = SimpleNamespace(objects=ObjectCatalog(),
                             eventPush=lambda *a, **k: pushed.append(a))
    reg = DatasetRegistry(DataCache(), events)
    return reg, pushed


def _add(reg, fp, parent=None, active=True):
    ds = SimpleNamespace(fingerprint=fp, parent=reg.get(parent), active=active,
                         isSubDataset=parent is not None, onDelete=lambda: None, getN=lambda: 1)
    reg._datasets[fp] = ds
    return ds


def test_deleting_a_dataset_deletes_everything_cut_from_it():
    reg, pushed = _registry()
    _add(reg, "D")
    _add(reg, "sub", parent="D")
    _add(reg, "sub-of-sub", parent="sub")
    _add(reg, "hidden", parent="D", active=False)   # SUB unticked
    _add(reg, "other")
    _add(reg, "other-sub", parent="other")

    reg.delete("D")

    assert set(reg.keys()) == {"other", "other-sub"}
    deleted = [a[1] for a in pushed if a[0] == "DATASET_DELETED"]
    assert sorted(deleted) == ["D", "hidden", "sub", "sub-of-sub"]
    # What was cut from a dataset goes before it, so no window is ever left
    # holding a subset whose parent it has already dropped.
    assert deleted.index("sub-of-sub") < deleted.index("sub") < deleted.index("D")
    assert deleted.index("hidden") < deleted.index("D")


def test_deleting_a_subset_leaves_its_parent():
    reg, pushed = _registry()
    _add(reg, "D")
    _add(reg, "sub", parent="D")

    reg.delete("sub")

    assert set(reg.keys()) == {"D"}
    assert [a[1] for a in pushed if a[0] == "DATASET_DELETED"] == ["sub"]
