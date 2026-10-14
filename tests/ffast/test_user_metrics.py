"""User metrics (ADR 0057 rules 2-5, 7, 14): the file store and the rules, over a
throwaway registry and a temporary folder."""
import numpy as np
import pytest

from ffast.config.user_metrics import (
    MetricConflict, MetricError, UserMetricStore, UserMetrics, metric_revision,
)
from ffast.config.models import ExprMetricConfig
from ffast.metrics import dims
from ffast.metrics.registry import MetricRegistry


@pytest.fixture
def registry():
    r = MetricRegistry()

    @r.metric(id="base.ref_e", inputs={"reference": "reference.energies"},
              shape=(dims.N_frames,), unit="energy")
    def ref_e(reference):
        return np.asarray(reference, dtype=float)

    @r.metric(id="base.atom_q", inputs={"v": "reference.atoms.charges"},
              shape=(dims.N_atoms,), unit="dimensionless")
    def atom_q(v):
        return np.asarray(v, dtype=float)

    r.freeze()
    return r


@pytest.fixture
def um(tmp_path, registry):
    m = UserMetrics(UserMetricStore(tmp_path / "metrics"), registry,
                    builtin_ids={"base.ref_e", "base.atom_q"})
    m.load()
    return m


def metric(id="user.double", expr="2 * e", **kw):
    return {"id": id, "expr": expr, "vars": {"e": "base.ref_e"}, **kw}


# ── files: the [[metrics.expr]] format ──────────────────────────────────────
def test_a_saved_metric_is_one_toml_file_in_the_project_format(um, tmp_path):
    um.save(metric(label="Double"))
    text = (tmp_path / "metrics" / "user.double.toml").read_text()
    assert "[[metrics.expr]]" in text and 'id = "user.double"' in text
    assert "[metrics.expr.vars]" in text and 'e = "base.ref_e"' in text


def test_a_file_is_loaded_at_start_and_registered(tmp_path, registry):
    UserMetricStore(tmp_path / "metrics").write(ExprMetricConfig(**metric()))
    m = UserMetrics(UserMetricStore(tmp_path / "metrics"), registry)
    assert m.load() == []
    assert registry.has("user.double")
    assert registry.get("user.double")[0].shape == (dims.N_frames,)


def test_export_is_the_file_content_without_header(um):
    um.save(metric())
    toml = um.export("user.double")
    assert toml.startswith("[[metrics.expr]]")
    assert 'expr = "2 * e"' in toml


# ── ids ─────────────────────────────────────────────────────────────────────
def test_a_taken_id_is_refused(um):
    with pytest.raises(MetricError, match="already taken"):
        um.save(metric(id="base.ref_e"))
    um.save(metric())
    with pytest.raises(MetricError, match="already taken"):
        um.save(metric())


@pytest.mark.parametrize("bad", ["nodot", "has space.x", ".x.y", "a..b"])
def test_an_unusable_id_is_refused(um, bad):
    with pytest.raises(MetricError):
        um.save(metric(id=bad))


def test_a_lab_style_id_is_allowed(um):
    um.save(metric(id="mylab.e_per_atom"))


def test_a_project_metric_wins_and_the_user_file_is_listed_hidden(tmp_path, registry):
    UserMetricStore(tmp_path / "metrics").write(ExprMetricConfig(**metric(id="base.ref_e")))
    m = UserMetrics(UserMetricStore(tmp_path / "metrics"), registry)
    m.load()
    [entry] = m.entries()
    assert entry["status"] == "hidden"
    assert "the project defines base.ref_e" in entry["message"]
    assert registry.get("base.ref_e")[0].label != "x"  # the project's stays


def test_a_hidden_file_cannot_be_edited_and_the_project_metric_is_untouched(tmp_path, registry):
    UserMetricStore(tmp_path / "metrics").write(ExprMetricConfig(**metric(id="base.ref_e")))
    m = UserMetrics(UserMetricStore(tmp_path / "metrics"), registry)
    m.load()
    before = registry.get("base.ref_e")
    with pytest.raises(MetricError, match="the project defines base.ref_e"):
        m.save(metric(id="base.ref_e", expr="3 * e"), previous_id="base.ref_e")
    assert registry.get("base.ref_e") is before
    m.delete("base.ref_e")                       # deleting the hidden file is allowed
    assert registry.get("base.ref_e") is before and m.entries() == []


def test_a_file_that_does_not_compile_stays_listed_with_the_reason(tmp_path, registry):
    UserMetricStore(tmp_path / "metrics").write(
        ExprMetricConfig(id="user.bad", expr="e + q", vars={"e": "base.ref_e", "q": "base.atom_q"}))
    m = UserMetrics(UserMetricStore(tmp_path / "metrics"), registry)
    assert [i for i, _ in m.load()] == ["user.bad"]
    assert not registry.has("user.bad")
    assert m.entries()[0]["status"] == "error"


def test_files_may_build_on_each_other_whatever_their_order(tmp_path, registry):
    store = UserMetricStore(tmp_path / "metrics")
    store.write(ExprMetricConfig(id="user.a_second", expr="x + 1", vars={"x": "user.z_first"}))
    store.write(ExprMetricConfig(id="user.z_first", expr="e * 2", vars={"e": "base.ref_e"}))
    m = UserMetrics(UserMetricStore(tmp_path / "metrics"), registry)
    assert m.load() == []
    assert registry.has("user.a_second")


# ── only user metrics are editable ──────────────────────────────────────────
def test_built_in_metrics_cannot_be_edited_or_deleted(um):
    with pytest.raises(MetricError, match="built-in"):
        um.save(metric(id="base.ref_e"), previous_id="base.ref_e")
    with pytest.raises(MetricError, match="built-in"):
        um.delete("base.ref_e")


def test_an_edit_keeps_the_id(um):
    um.save(metric())
    with pytest.raises(MetricError, match="keeps"):
        um.save(metric(id="user.other"), previous_id="user.double")


# ── editing on a running server (rule 5) ────────────────────────────────────
def test_an_edit_replaces_the_formula_and_reports_what_is_affected(um, registry):
    um.save(metric())
    um.save({"id": "user.quad", "expr": "x * 2", "vars": {"x": "user.double"}})
    before = registry.get("user.double")[1].implementation_source()
    change = um.save(metric(expr="3 * e"), previous_id="user.double")
    assert registry.get("user.double")[1].implementation_source() != before
    assert change.affected == {"user.double", "user.quad"}


def test_a_failed_edit_leaves_registry_and_file_as_they_were(um, registry, tmp_path):
    um.save(metric())
    text = (tmp_path / "metrics" / "user.double.toml").read_text()
    with pytest.raises(MetricError):
        um.save(metric(expr="nope + 1"), previous_id="user.double")
    assert registry.has("user.double")
    assert registry.get("user.double")[1].expr == "2 * e"
    assert (tmp_path / "metrics" / "user.double.toml").read_text() == text


def test_an_edit_that_would_change_a_dependents_shape_is_refused(um, registry):
    um.save(metric())
    um.save({"id": "user.quad", "expr": "x * 2", "vars": {"x": "user.double"}})
    with pytest.raises(MetricError, match="user.quad"):
        um.save({"id": "user.double", "expr": "q", "vars": {"q": "base.atom_q"}},
                previous_id="user.double")
    assert registry.get("user.double")[0].shape == (dims.N_frames,)


def test_a_cycle_is_refused(um, registry):
    um.save(metric())
    um.save({"id": "user.quad", "expr": "x * 2", "vars": {"x": "user.double"}})
    with pytest.raises(MetricError, match="cycle"):
        um.save({"id": "user.double", "expr": "x", "vars": {"x": "user.quad"}},
                previous_id="user.double")
    assert registry.get("user.double")[1].expr == "2 * e"


# ── versions (rule 6) ───────────────────────────────────────────────────────
def test_a_stale_save_is_refused_unless_overwriting(um):
    um.save(metric())
    first = metric_revision(ExprMetricConfig(**metric()))
    um.save(metric(expr="3 * e"), previous_id="user.double", base_revision=first)
    with pytest.raises(MetricConflict, match="another window"):
        um.save(metric(expr="4 * e"), previous_id="user.double", base_revision=first)
    um.save(metric(expr="4 * e"), previous_id="user.double", base_revision=first, overwrite=True)
    assert um.get("user.double").expr == "4 * e"


def test_entries_carry_the_revision_the_editor_sends_back(um):
    um.save(metric())
    [entry] = um.entries()
    um.save(metric(expr="5 * e"), previous_id="user.double", base_revision=entry["revision"])


# ── delete (rule 7) ─────────────────────────────────────────────────────────
def test_delete_removes_file_and_registration(um, registry, tmp_path):
    um.save(metric())
    change = um.delete("user.double")
    assert not registry.has("user.double")
    assert not (tmp_path / "metrics" / "user.double.toml").exists()
    assert change.affected == {"user.double"}


def test_delete_is_refused_while_another_metric_uses_it(um, registry):
    um.save(metric())
    um.save({"id": "user.quad", "expr": "x * 2", "vars": {"x": "user.double"}})
    with pytest.raises(MetricError, match="metric user.quad"):
        um.delete("user.double")
    assert registry.has("user.double")


def test_delete_is_refused_while_a_provider_reports_a_user(um):
    um.save(metric())
    um.usage_providers.append(lambda mid: ["tab Mine, panel 1"] if mid == "user.double" else [])
    with pytest.raises(MetricError, match="tab Mine, panel 1"):
        um.delete("user.double")
    um.usage_providers.clear()
    um.delete("user.double")


def test_used_by_lists_every_user_for_the_pane(um):
    um.save(metric())
    um.save({"id": "user.quad", "expr": "x * 2", "vars": {"x": "user.double"}})
    entries = {e["id"]: e for e in um.entries()}
    assert entries["user.double"]["used_by"] == ["metric user.quad"]
    assert entries["user.quad"]["used_by"] == []


def test_delete_also_drops_derived_transform_metrics(um, registry):
    um.save(metric())

    @registry.metric(id="user.double__kde__p1", inputs={"d": "user.double"},
                     shape=(dims.curve_xy, dims.grid), unit="x")
    def derived(d):
        return np.zeros((2, 3))

    um.delete("user.double")
    assert not registry.has("user.double__kde__p1")


# ── checking without saving (rule 11) ───────────────────────────────────────
def test_check_reports_the_result_shape_and_registers_nothing(um, registry):
    out = um.check(metric(id="user.try"))
    assert out == {"ok": True, "error": None, "shape": "N_frames"}
    assert not registry.has("user.try")


def test_check_reports_an_error(um):
    out = um.check(metric(expr="e +"))
    assert out["ok"] is False and "valid expression" in out["error"]


def test_check_of_an_edit_does_not_see_the_old_self(um):
    um.save(metric())
    out = um.check({"id": "user.double", "expr": "d + 1", "vars": {"d": "user.double"}})
    assert out["ok"] is False


# ── origin ──────────────────────────────────────────────────────────────────
def test_origin_tells_user_project_and_built_in_apart(tmp_path, registry):
    @registry.metric(id="proj.extra", inputs={"r": "reference.energies"},
                     shape=(dims.N_frames,), unit="energy")
    def extra(r):
        return r
    m = UserMetrics(UserMetricStore(tmp_path / "m"), registry, builtin_ids={"base.ref_e", "base.atom_q"})
    m.load()
    m.save(metric())
    assert (m.origin("user.double"), m.origin("proj.extra"), m.origin("base.ref_e")) == (
        "user", "project", "built-in")


# ── the table can change on a running server ────────────────────────────────
def test_registry_dependents_are_transitive(registry):
    from ffast.metrics.expr import compile_expr_metric
    compile_expr_metric("a.one", "e + 1", {"e": "base.ref_e"}, registry=registry)
    compile_expr_metric("a.two", "e + 1", {"e": "a.one"}, registry=registry)
    assert registry.dependents_of("base.ref_e") == ["a.one", "a.two"]
    assert registry.direct_dependents_of("base.ref_e") == ["a.one"]
