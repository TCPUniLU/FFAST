"""Slice 3 regression: deleting a model/dataset now prunes its >3-segment keys.

This is the bug the deepening exists to kill. The old prune filters matched only
``len(split("__")) == 3``, so a params metric key (4 segments) or a Transform
Metric key (5+ segments, identity contains ``__``) survived the delete and rotted
in the cache — the delete-not-persisted family. Right-anchored ``matches_model`` /
``matches_dataset`` prune them regardless of segment count.

Exercised through the real ``ModelRegistry`` / ``DatasetRegistry`` with a real
``DataCache`` and tiny fakes for the model/dataset and event bus — no env/Qt.
"""

from types import SimpleNamespace

from client.data_cache import DataCache
from client.model_registry import ModelRegistry
from client.dataset_registry import DatasetRegistry
from client.object_catalog import ObjectCatalog

# The two keys the old len==3 filter leaked.
PARAMS_KEY = "ffast.force_mae__pdeadbeef__M__D"          # 4 segments
TRANSFORM_KEY = "ffast.force_mae__kde__p1a2b3c4__M__D"   # 5 segments (identity has __)


def _events():
    catalog = ObjectCatalog()
    catalog.register("M", {})
    catalog.register("D", {})
    return SimpleNamespace(objects=catalog,
                           eventPush=lambda *a, **k: None)


def test_the_leaked_keys_really_are_not_three_segments():
    # Document why they leaked: the old predicate required exactly 3 segments.
    assert len(PARAMS_KEY.split("__")) != 3
    assert len(TRANSFORM_KEY.split("__")) != 3


def _seed_cache():
    cache = DataCache()
    cache["forces__M__D"] = object()        # raw prediction key for M (3 seg)
    cache["energy__M__D"] = object()
    cache[PARAMS_KEY] = object()            # M, would have leaked
    cache[TRANSFORM_KEY] = object()         # M, would have leaked
    cache["forces__OTHER__D"] = object()    # different model — must survive
    cache["ffast.force_mae__kde__p1a2b3c4__OTHER__D"] = object()  # different model — survive
    return cache


def test_delete_model_prunes_params_and_transform_keys():
    cache = _seed_cache()
    reg = ModelRegistry(cache, _events())
    reg._models["M"] = SimpleNamespace(fingerprint="M", onDelete=lambda: None)

    reg.delete("M")

    # Every M key gone — including the >3-segment ones that used to leak.
    assert "forces__M__D" not in cache
    assert "energy__M__D" not in cache
    assert PARAMS_KEY not in cache
    assert TRANSFORM_KEY not in cache
    # Other model's keys untouched.
    assert "forces__OTHER__D" in cache
    assert "ffast.force_mae__kde__p1a2b3c4__OTHER__D" in cache


def test_delete_dataset_prunes_params_and_transform_keys():
    cache = DataCache()
    cache["forces__M__D"] = object()
    cache[PARAMS_KEY] = object()                                  # dataset D, would have leaked
    cache[TRANSFORM_KEY] = object()                               # dataset D, would have leaked
    cache["forces__M__OTHERDS"] = object()                        # different dataset — survive
    cache["ffast.force_mae__kde__p1a2b3c4__M__OTHERDS"] = object()  # survive

    reg = DatasetRegistry(cache, _events())
    reg._datasets["D"] = SimpleNamespace(fingerprint="D", onDelete=lambda: None)

    reg.delete("D")

    assert "forces__M__D" not in cache
    assert PARAMS_KEY not in cache
    assert TRANSFORM_KEY not in cache
    assert "forces__M__OTHERDS" in cache
    assert "ffast.force_mae__kde__p1a2b3c4__M__OTHERDS" in cache


def test_delete_also_prunes_object_metadata():
    cache = _seed_cache()
    events = _events()
    reg = ModelRegistry(cache, events)
    reg._models["M"] = SimpleNamespace(fingerprint="M", onDelete=lambda: None)

    reg.delete("M")

    assert "M" not in events.objects
