"""Slice 1 of the Cache Key deepening: the pure value type is the test surface.

No Environment, no Qt, no socket — exercising the interface fully exercises the
behaviour. Covers the two bugs the deepening exists to kill:

* right-anchored parse over a ``__``-laden Metric ID (the positional mis-decode), and
* ``matches_model`` / ``matches_dataset`` over those keys (the prune leak that
  ``len(p) == 3`` filters caused).
"""

import pytest

from ffast.cache import CacheKey, PredictionArrayKey


# ── round-trip / format ───────────────────────────────────────────────────────

@pytest.mark.parametrize("key", [
    CacheKey("forces", "m1", "d1"),
    CacheKey("energy", None, "d1"),                 # model-independent
    CacheKey("gyration", "m1", None),               # dataset-independent
    CacheKey("constant", None, None),               # both independent
    CacheKey("ffast.force_mae__kde__p1a2b3c4", "m1", "d1"),   # transform metric id with __
    CacheKey("forces", "zeroModel", "d1"),          # non-hex fingerprint (zero baseline)
])
def test_format_parse_round_trip(key):
    assert CacheKey.parse(key.format()) == key


def test_format_uses_nil_sentinel_for_absent_slots():
    assert CacheKey("constant", None, None).format() == "constant__nil__nil"


def test_nil_in_string_parses_back_to_none():
    k = CacheKey.parse("energy__nil__d1")
    assert k.model_fp is None and k.dataset_fp == "d1"


# ── right-anchoring: the soundness fix ────────────────────────────────────────

def test_right_anchor_keeps_double_underscore_identity_intact():
    # The Metric ID itself contains __ ; model/dataset are the last two segments.
    k = CacheKey.parse("ffast.force_mae__kde__p1a2b3c4__MODELFP__DATASETFP")
    assert k.dtype == "ffast.force_mae__kde__p1a2b3c4"
    assert k.model_fp == "MODELFP"
    assert k.dataset_fp == "DATASETFP"


def test_legacy_four_field_params_key_decodes_correctly():
    # Old make_metric_cache_key params branch: identity__paramshash__model__dataset.
    # Right-anchoring folds the hash into the identity; model/dataset still recovered.
    k = CacheKey.parse("ffast.force_mae__abcd1234__MODELFP__DATASETFP")
    assert k.model_fp == "MODELFP"
    assert k.dataset_fp == "DATASETFP"
    assert k.dtype == "ffast.force_mae__abcd1234"


# ── the questions callers ask (prune-leak regression) ─────────────────────────

def test_matches_model_and_dataset_on_underscore_laden_key():
    k = CacheKey.parse("ffast.force_mae__kde__p1__MODELFP__DATASETFP")
    assert k.matches_model("MODELFP")
    assert not k.matches_model("OTHER")
    assert k.matches_dataset("DATASETFP")
    assert not k.matches_dataset("OTHER")


def test_matches_model_false_when_model_independent():
    assert not CacheKey("energy", None, "d1").matches_model("m1")


# ── S1 validation: fail fast, don't silently mis-decode ───────────────────────

@pytest.mark.parametrize("bad", ["a__b", "single", "", "a"])
def test_parse_rejects_too_few_segments(bad):
    with pytest.raises(ValueError):
        CacheKey.parse(bad)


def test_parse_rejects_non_string():
    with pytest.raises(ValueError):
        CacheKey.parse(None)  # type: ignore[arg-type]


def test_try_parse_returns_none_instead_of_raising():
    assert CacheKey.try_parse("malformed") is None
    assert CacheKey.try_parse("forces__m1__d1") == CacheKey("forces", "m1", "d1")


def test_constructor_rejects_empty_dtype():
    with pytest.raises(ValueError):
        CacheKey("", "m1", "d1")


def test_constructor_rejects_nil_literal_in_slot():
    # "nil" is the wire sentinel; in memory the absent slot must be None.
    with pytest.raises(ValueError):
        CacheKey("forces", "nil", "d1")


def test_constructor_rejects_underscore_in_fingerprint_slot():
    with pytest.raises(ValueError):
        CacheKey("forces", "a__b", "d1")


# ── hashability (S2 readiness: usable directly as the cache dict key) ──────────

def test_usable_as_dict_key():
    cache = {}
    k1 = CacheKey("forces", "m1", "d1")
    cache[k1] = "payload"
    # An independently-constructed equal key looks up the same entry.
    assert cache[CacheKey("forces", "m1", "d1")] == "payload"


def test_equal_keys_hash_equal():
    assert hash(CacheKey("forces", "m1", "d1")) == hash(CacheKey("forces", "m1", "d1"))


# ── PredictionArrayKey sibling (transfer namespace) ───────────────────────────

def test_prediction_array_key_round_trip():
    k = PredictionArrayKey("forces", "MODELFP")
    assert k.format() == "pred__forces__MODELFP"
    assert PredictionArrayKey.parse(k.format()) == k


def test_prediction_array_key_recognition():
    assert PredictionArrayKey.is_prediction_key("pred__energy__m1")
    assert not PredictionArrayKey.is_prediction_key("forces__m1__d1")


def test_prediction_array_key_rejects_non_prediction_string():
    with pytest.raises(ValueError):
        PredictionArrayKey.parse("forces__m1__d1")


def test_prediction_array_key_has_no_dataset_slot():
    # A cache key and a prediction key with overlapping text don't collide:
    # the prediction key carries no dataset, the cache key does.
    assert not hasattr(PredictionArrayKey("forces", "m1"), "dataset_fp")
