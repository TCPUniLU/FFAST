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


def test_an_edit_that_would_change_an_inheriting_transforms_shape_is_refused(um, registry, tmp_path):
    """ADR 0061 decision 7: a transform that takes its source's shape would keep
    the old one, so the edit is refused, the same as for a formula dependent."""
    from ffast.metrics.transforms import compile_transform
    um.save(metric())
    tid = compile_transform("user.double", "abs", registry=registry)     # shape "inherit"
    before = (tmp_path / "metrics" / "user.double.toml").read_text()
    with pytest.raises(MetricError, match="another shape") as exc:
        um.save({"id": "user.double", "expr": "q", "vars": {"q": "base.atom_q"}},
                previous_id="user.double")
    assert tid in str(exc.value)
    assert registry.get("user.double")[0].shape == (dims.N_frames,)
    assert registry.get("user.double")[1].expr == "2 * e"
    assert (tmp_path / "metrics" / "user.double.toml").read_text() == before


def test_an_edit_that_keeps_the_shape_is_fine_for_a_transform(um, registry):
    from ffast.metrics.transforms import compile_transform
    um.save(metric())
    compile_transform("user.double", "abs", registry=registry)
    um.save(metric(expr="3 * e"), previous_id="user.double")


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


def test_editing_a_metric_deleted_elsewhere_is_a_conflict_that_save_anyway_resolves(um):
    um.save(metric())
    first = metric_revision(ExprMetricConfig(**metric()))
    um.delete("user.double")
    with pytest.raises(MetricConflict, match="Deleted in another window"):
        um.save(metric(expr="3 * e"), previous_id="user.double", base_revision=first)
    um.save(metric(expr="3 * e"), previous_id="user.double", base_revision=first, overwrite=True)
    assert um.get("user.double").expr == "3 * e"


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


def test_used_by_names_the_users_and_entries_do_not_carry_it(um):
    um.save(metric())
    um.save({"id": "user.quad", "expr": "x * 2", "vars": {"x": "user.double"}})
    assert all("used_by" not in e for e in um.entries())
    assert um.used_by("user.double") == ["metric user.quad"]
    assert um.used_by("user.quad") == []


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
    assert out == {"ok": True, "error": None, "shape": "N_frames", "warnings": []}
    assert not registry.has("user.try")


def test_check_reports_an_error(um):
    out = um.check(metric(expr="e +"))
    assert out["ok"] is False and "valid expression" in out["error"]


def test_check_of_an_edit_does_not_see_the_old_self(um):
    um.save(metric())
    out = um.check({"id": "user.double", "expr": "d + 1", "vars": {"d": "user.double"}})
    assert out["ok"] is False


DIFFERENT = ("by=atom needs structures with the same atoms in the same order; "
             "this dataset's structures differ")


def by_atom(**kw):
    return {"id": "user.q_mean", "expr": "mean(q, by=atom)", "vars": {"q": "base.atom_q"}, **kw}


def test_check_warns_of_by_atom_on_a_dataset_whose_structures_differ(um):
    # ADR 0066 rule 5: a warning, not a refusal; it may be right elsewhere
    out = um.check(by_atom(), uniform=False)
    assert out["ok"] is True and out["warnings"] == [DIFFERENT]


def test_check_does_not_warn_where_structures_match_or_the_dataset_is_unknown(um):
    assert um.check(by_atom(), uniform=True)["warnings"] == []
    assert um.check(by_atom())["warnings"] == []


def test_check_warns_of_by_atom_in_a_metric_the_formula_takes(um):
    um.save(by_atom())
    out = um.check({"id": "user.twice", "expr": "2 * m", "vars": {"m": "user.q_mean"}},
                   uniform=False)
    assert out["warnings"] == [DIFFERENT]


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


# ── sessions carry formulas (rules 15, 16) ──────────────────────────────────
from ffast.config.user_metrics import (  # noqa: E402
    SESSION_FILE, SessionPlan, formulas_toml, read_formulas,
)


def _cfg(id, expr="2 * e", **vars_):
    return ExprMetricConfig(id=id, expr=expr, vars=vars_ or {"e": "base.ref_e"})


def test_a_session_carries_every_user_metric_that_compiled(um, tmp_path):
    um.save(metric(id="user.base", expr="e"))
    um.save({"id": "user.top", "expr": "b * 2", "vars": {"b": "user.base"}})
    UserMetricStore(tmp_path / "metrics").write(ExprMetricConfig(id="user.hidden", expr="e", vars={"e": "base.ref_e"}))
    assert [c.id for c in um.session_formulas()] == ["user.base", "user.top"]


def test_a_server_without_user_metrics_gives_a_session_nothing(um):
    assert um.session_formulas() == []


def test_the_formulas_file_round_trips_in_the_project_format(tmp_path):
    cfgs = [_cfg("user.a", label="A", unit="energy"), _cfg("user.b", "e + 1")]
    path = tmp_path / SESSION_FILE
    path.write_text(formulas_toml(cfgs))
    assert path.read_text().count("\n[[metrics.expr]]\n") == 2
    assert read_formulas(path) == cfgs
    assert read_formulas(tmp_path / "missing.toml") == []


def test_a_metric_the_server_lacks_is_added_as_it_is(um):
    plan = um.plan_session([_cfg("user.new")])
    assert [c.id for c in plan.add] == ["user.new"] and plan.renamed == {}
    um.add_session(plan)
    assert um.get("user.new").expr == "2 * e"


def test_an_identical_formula_adds_nothing(um):
    um.save(metric(id="user.same"))
    assert not um.plan_session([_cfg("user.same")])


def test_a_clash_keeps_both_the_session_version_as_id_dot_2(um, registry):
    um.save(metric(id="user.x", expr="3 * e"))
    plan = um.plan_session([_cfg("user.x", "2 * e")])
    assert plan.renamed == {"user.x": "user.x.2"}
    assert [(c.id, c.expr) for c in plan.add] == [("user.x.2", "2 * e")]
    assert plan.describe()[0]["clash_with"] == "user.x"
    um.add_session(plan)
    assert registry.get("user.x")[1].expr == "3 * e"            # the server's is untouched
    assert registry.get("user.x.2")[1].expr == "2 * e"


def test_the_next_free_number_is_used(um):
    um.save(metric(id="user.x", expr="3 * e"))
    um.save(metric(id="user.x.2", expr="4 * e"))
    assert um.plan_session([_cfg("user.x", "2 * e")]).renamed == {"user.x": "user.x.3"}


def test_a_clash_with_a_built_in_or_project_metric_is_also_kept_as_dot_2(um):
    plan = um.plan_session([_cfg("base.ref_e", "2 * e")])
    assert plan.renamed == {"base.ref_e": "base.ref_e.2"}


def test_the_sessions_own_formulas_follow_a_renamed_one(um, registry):
    um.save(metric(id="user.base", expr="3 * e"))
    plan = um.plan_session([
        _cfg("user.base", "2 * e"),
        ExprMetricConfig(id="user.top", expr="b + 1", vars={"b": "user.base"}),
    ])
    assert plan.renamed["user.base"] == "user.base.2"
    top = next(c for c in plan.add if c.id == "user.top")
    assert dict(top.vars) == {"b": "user.base.2"}               # built on the session's version
    um.add_session(plan)
    assert registry.get("user.top")[0].inputs["b"] == "user.base.2"


def test_a_formula_built_on_one_that_clashed_is_itself_a_clash_if_the_server_has_it(um):
    um.save(metric(id="user.base", expr="3 * e"))
    um.save({"id": "user.top", "expr": "b + 1", "vars": {"b": "user.base"}})
    plan = um.plan_session([
        _cfg("user.base", "2 * e"),
        ExprMetricConfig(id="user.top", expr="b + 1", vars={"b": "user.base"}),
    ])
    # user.top is textually the same, but it would now sit on user.base.2: a different metric
    assert plan.renamed == {"user.base": "user.base.2", "user.top": "user.top.2"}


def test_add_session_orders_formulas_that_build_on_each_other(um):
    top = ExprMetricConfig(id="user.top", expr="b + 1", vars={"b": "user.base"})
    plan = um.plan_session([top, _cfg("user.base")])           # dependent listed first
    um.add_session(plan)
    assert um.get("user.top") and um.get("user.base")


def test_an_empty_plan_is_false(um):
    assert not SessionPlan()
