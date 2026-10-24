"""Slice 4 (S2): DataCache is the single Cache Key validation boundary.

A malformed key can't enter the store; callers may index with a flat string or a
CacheKey object interchangeably; storage and the iteration surface stay
string-keyed so disk/wire/sweep sites are untouched (canonicalization is identity
for well-formed strings).
"""

import pytest

from ffast.cache.store import DataCache
from ffast.cache import CacheKey


def test_string_round_trip_is_identity():
    c = DataCache()
    c["forces__M__D"] = "v"
    assert c["forces__M__D"] == "v"
    assert list(c.keys()) == ["forces__M__D"]      # stored string unchanged


def test_str_and_cachekey_index_the_same_entry():
    c = DataCache()
    c["forces__M__D"] = "v"
    # read with a CacheKey object
    assert c[CacheKey("forces", "M", "D")] == "v"
    assert CacheKey("forces", "M", "D") in c
    assert c.get(CacheKey("forces", "M", "D")) == "v"


def test_write_with_cachekey_reads_with_string():
    c = DataCache()
    c[CacheKey("energy", None, "D")] = "v"          # model-independent
    assert c["energy__nil__D"] == "v"               # serializes via the nil sentinel
    assert "energy__nil__D" in c


def test_setitem_rejects_malformed_key():
    c = DataCache()
    with pytest.raises(ValueError):
        c["only_one_segment"] = "v"                 # < 3 segments -> cannot be a Cache Key


def test_reads_of_malformed_key_degrade_to_absent():
    c = DataCache()
    c["forces__M__D"] = "v"
    assert c.get("garbage") is None                 # no raise
    assert "garbage" not in c
    with pytest.raises(KeyError):
        _ = c["garbage"]


def test_transform_key_with_underscores_round_trips_through_cache():
    c = DataCache()
    key = "ffast.force_difference[atom+structure;length]__kde__p1a2b3c4__M__D"
    c[key] = "v"
    assert c[key] == "v"
    assert c[CacheKey.parse(key)] == "v"            # CacheKey lookup hits the same entry
    assert list(c.keys()) == [key]                  # identity preserved


def test_delete_accepts_str_and_cachekey():
    c = DataCache()
    c["forces__M__D"] = "v"
    del c[CacheKey("forces", "M", "D")]
    assert "forces__M__D" not in c


def test_invalidate_still_sees_string_keys():
    c = DataCache()
    c["forces__M__D"] = "v"
    c["forces__OTHER__D"] = "v"
    dead = c.invalidate(lambda k: CacheKey.parse(k).matches_model("M"))
    assert dead == ["forces__M__D"]
    assert "forces__OTHER__D" in c


def test_invalidate_metrics_drops_a_metric_whatever_its_model_dataset_and_params():
    """ADR 0057 rule 5: the outer key holds the metric id, not its formula."""
    from types import SimpleNamespace

    from ffast.core.data_service import DataService

    cache = DataCache()
    for key in ("user.m__mod__ds", "user.m__p1a2b3c4__mod__ds", "user.m__nil__ds2",
                "user.m2__mod__ds", "forces__mod__ds"):
        cache[key] = object()
    dropped = DataService.invalidate_metrics(SimpleNamespace(cache=cache), {"user.m"})
    assert sorted(dropped) == ["user.m__mod__ds", "user.m__nil__ds2", "user.m__p1a2b3c4__mod__ds"]
    assert sorted(cache.keys()) == ["forces__mod__ds", "user.m2__mod__ds"]
