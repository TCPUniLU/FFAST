"""Slice 2: the two key *builders* now route through CacheKey.format().

Both `DataType.getCacheKey` and `DataService.make_metric_cache_key` read only
their arguments (not instance state) to build the string, so they're exercised
as unbound methods with a SimpleNamespace stand-in for `self` and tiny fakes for
model/dataset — no Environment/Qt construction needed.

The point of the slice is PARITY: for params-less keys the emitted string must be
byte-identical to the legacy format so in-session cache lookups keep hitting.
"""

from types import SimpleNamespace

import pytest

from ffast.cache import CacheKey
from ffast.core.data_types import DataType
from ffast.core.data_service import DataService


def _dt(key, model_dep, dataset_dep):
    return SimpleNamespace(key=key, modelDependent=model_dep, datasetDependent=dataset_dep)


def _obj(fp):
    return SimpleNamespace(fingerprint=fp)


# ── DataType.getCacheKey ──────────────────────────────────────────────────────

def test_getcachekey_parity_model_and_dataset_dependent():
    key = DataType.getCacheKey(_dt("forces", True, True), model=_obj("M"), dataset=_obj("D"))
    assert key == "forces__M__D"                       # byte-identical to legacy
    assert CacheKey.parse(key) == CacheKey("forces", "M", "D")


def test_getcachekey_model_independent_emits_nil():
    key = DataType.getCacheKey(_dt("energy", False, True), dataset=_obj("D"))
    assert key == "energy__nil__D"


def test_getcachekey_both_independent_emits_two_nils():
    key = DataType.getCacheKey(_dt("constant", False, False))
    assert key == "constant__nil__nil"


def test_getcachekey_accepts_string_fingerprint():
    key = DataType.getCacheKey(_dt("forces", True, True), model="M", dataset="D")
    assert key == "forces__M__D"


def test_getcachekey_returns_none_when_required_model_missing():
    assert DataType.getCacheKey(_dt("forces", True, True), model=None, dataset=_obj("D")) is None


# ── DataService.make_metric_cache_key ─────────────────────────────────────────

def test_make_metric_key_parity_no_params():
    key = DataService.make_metric_cache_key(SimpleNamespace(), "ffast.force_mae", {}, _obj("M"), _obj("D"))
    assert key == "ffast.force_mae__M__D"              # byte-identical to legacy 3-part form
    assert CacheKey.parse(key) == CacheKey("ffast.force_mae", "M", "D")


def test_make_metric_key_nil_slots_when_model_or_dataset_none():
    assert DataService.make_metric_cache_key(SimpleNamespace(), "m", {}, None, _obj("D")) == "m__nil__D"
    assert DataService.make_metric_cache_key(SimpleNamespace(), "m", {}, None, None) == "m__nil__nil"


def test_make_metric_key_folds_params_into_identity():
    key = DataService.make_metric_cache_key(
        SimpleNamespace(), "ffast.force_mae", {"norm": "l2"}, _obj("M"), _obj("D")
    )
    parsed = CacheKey.parse(key)
    assert parsed.model_fp == "M" and parsed.dataset_fp == "D"      # right-anchor still recovers them
    assert parsed.dtype.startswith("ffast.force_mae__p")           # params live in the identity, not a 4th field


def test_make_metric_key_params_are_deterministic_and_distinct():
    a1 = DataService.make_metric_cache_key(SimpleNamespace(), "m", {"norm": "l1"}, _obj("M"), _obj("D"))
    a2 = DataService.make_metric_cache_key(SimpleNamespace(), "m", {"norm": "l1"}, _obj("M"), _obj("D"))
    b = DataService.make_metric_cache_key(SimpleNamespace(), "m", {"norm": "l2"}, _obj("M"), _obj("D"))
    assert a1 == a2          # same params -> same key (cache hit)
    assert a1 != b           # different params -> different key (cache miss)


def test_make_metric_key_transform_id_with_underscores_round_trips():
    # A compiled Transform Metric id already carries __ ; right-anchoring keeps it whole.
    key = DataService.make_metric_cache_key(
        SimpleNamespace(), "ffast.force_mae__kde__p1a2b3c4", {}, _obj("M"), _obj("D")
    )
    assert key == "ffast.force_mae__kde__p1a2b3c4__M__D"
    assert CacheKey.parse(key).dtype == "ffast.force_mae__kde__p1a2b3c4"


# ── formula identity in the key (ADR 0057 rule 5) ─────────────────────────────

def _formula_registry(monkeypatch):
    from ffast.metrics.expr import compile_expr_metric
    from ffast.metrics.registry import MetricRegistry
    import sys
    registry_module = sys.modules["ffast.metrics.registry"]

    reg = MetricRegistry()
    reg.metric(id="t.base", inputs={"x": "ref.x"}, shape="scalar", unit="energy")(lambda x: x)
    monkeypatch.setattr(registry_module, "default_registry", reg)
    return reg, compile_expr_metric


def _key(mid):
    return DataService.make_metric_cache_key(SimpleNamespace(), mid, {}, _obj("M"), _obj("D"))


def test_formula_metric_key_changes_when_the_formula_is_edited(monkeypatch):
    reg, compile_ = _formula_registry(monkeypatch)
    compile_("lab.a", "x * 2", {"x": "t.base"}, registry=reg)
    before = _key("lab.a")
    reg.unregister("lab.a")
    compile_("lab.a", "x * 3", {"x": "t.base"}, registry=reg)
    assert _key("lab.a") != before
    assert _key("lab.a").startswith("lab.a__f")


def test_formula_built_on_a_formula_changes_when_the_lower_one_does(monkeypatch):
    reg, compile_ = _formula_registry(monkeypatch)
    compile_("lab.a", "x * 2", {"x": "t.base"}, registry=reg)
    compile_("lab.b", "a + 1", {"a": "lab.a"}, registry=reg)
    before = _key("lab.b")
    reg.unregister("lab.a")
    compile_("lab.a", "x * 3", {"x": "t.base"}, registry=reg)
    assert _key("lab.b") != before


def test_builtin_style_metric_key_is_unchanged(monkeypatch):
    _formula_registry(monkeypatch)
    assert _key("t.base") == "t.base__M__D"

