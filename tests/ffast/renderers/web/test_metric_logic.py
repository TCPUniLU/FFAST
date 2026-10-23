"""The Metrics pane's decisions, as pure functions (ADR 0057).

``metric_logic.js`` has no DOM and no socket, so each rule is evaluated here
directly in one browser launch, the way test_web_pure_helpers.py does for the
other helpers: a static file server gives an http origin for the ES module, and
every case runs in one batch.
"""
from __future__ import annotations

import asyncio
import functools
import http.server
import json
import threading
from pathlib import Path

import pytest

pytestmark = pytest.mark.integration

STATIC_DIR = (
    Path(__file__).resolve().parents[4] / "ffast" / "renderers" / "web" / "static"
)

INPUTS = (
    "[{ref:'reference.energies',name:'ref_E',label:'Energy',group:'Reference',only:null},"
    " {ref:'prediction.energies',name:'pred_E',label:'Energy',group:'Prediction',only:null},"
    " {ref:'reference.positions',name:'ref_R',label:'Positions',group:'Reference',only:'reference'},"
    " {ref:'reference.info.REF_energy',name:'ref_REF_energy',label:'REF_energy',group:'File keys',only:null},"
    " {ref:'ffast.force_mae',name:'force_mae',label:'Force MAE',group:'Metrics',only:null}]"
)

CASES = {
    # ── which names a formula uses ──────────────────────────────────────────
    "names_skip_functions": "ml.valueNames('norm(pred_F - ref_F) / n_atoms')",
    "dup_formula": "ml.duplicateDraft({id: 'p.e', label: 'E', unit: 'energy', expr: '2 * e', vars: {e: 'reference.energies'}})",
    "dup_equivalent": ("ml.duplicateDraft({id: 'ffast.force_mae', label: 'Force MAE', unit: 'force', expr: null,"
                       " equivalent_expr: 'norm(pred_F - ref_F)', equivalent_vars: {pred_F: 'prediction.forces', ref_F: 'reference.forces'}})"),
    "dup_wrapper": "ml.duplicateDraft({id: 'ffast.force_mae_per_structure', label: 'MAE', unit: 'force', expr: null})",
    "dup_wrapper_not_a_function": "ml.duplicateDraft({id: 'my.x', label: '', unit: '', expr: null}).expr",
    "names_x_call_is_not_a_value": "ml.valueNames('x(F) + y(F)')",
    "names_a_variable_called_x": "ml.valueNames('x + 1')",
    "names_numbers_with_exponents": "ml.valueNames('a * 1e5 + 2e-3')",
    "names_skip_by_words_and_symbols": (
        "ml.valueNames('mean(norm(pF - rF), by=structure, where=element(\"H\", \"C\")) + sum(e, by = all)')"),
    "names_keep_a_comparison_and_a_variable_called_all": "ml.valueNames('where(a == all, all, 0)')",
    "rename_leaves_words_and_text": (
        "ml.renameVariable('mean(all, by=all, where=element(\"all\"))', 'all', 'v')"),
    "suggest_by_words": (
        "[ml.suggestions('mean(e, by=s', 12, ['e'], []).map(s => [s.text, s.kind]),"
        " ml.suggestions('mean(e, by=', 11, ['e'], []).length,"
        " ml.suggestions('me', 2, [], []).map(s => s.text)]"),
    "undefined_ignores_constants": "ml.undefinedNames('pi * a + n_atoms', ['a'])",
    "undefined_lists_unknown": "ml.undefinedNames('a + b + c', ['a'])",

    # ── Fix ─────────────────────────────────────────────────────────────────
    "fix_by_short_name": f"ml.fixFor('ref_E', {INPUTS})?.ref",
    "fix_ignores_case": f"ml.fixFor('REF_e', {INPUTS})?.ref",
    "fix_by_label_when_unique": f"ml.fixFor('Positions', {INPUTS})?.ref",
    "fix_none_when_several_match": f"ml.fixFor('energies', {INPUTS})",
    "fix_none_when_nothing_matches": f"ml.fixFor('nope', {INPUTS})",

    # ── editing the text ────────────────────────────────────────────────────
    "unique_name": "[ml.uniqueName('a', []), ml.uniqueName('a', ['a']), ml.uniqueName('a', ['a', 'a_2'])]",
    "insert_replaces_selection": "ml.insertAt('abc + d', 0, 3, 'pred_E')",
    "insert_at_caret": "ml.insertAt('a + ', 4, 4, 'b')",
    "complete_replaces_the_typed_part": "ml.completeAt('2 * pre', 7, 'pred_E')",
    "rename_changes_values_only": "ml.renameVariable('x(F) + F * x', 'x', 'k')",
    "rename_whole_names_only": "ml.renameVariable('ab + a + abc', 'a', 'q')",
    "variable_name_problems": (
        "['1a', 'pi', 'a b', 'ok', 'dup'].map(n => ml.variableNameProblem(n, ['dup']))"),

    # ── suggestions while typing ────────────────────────────────────────────
    "suggest_nothing_without_a_prefix": f"ml.suggestions('2 * ', 4, [], {INPUTS})",
    "suggest_vars_then_inputs_then_functions": (
        f"ml.suggestions('pre', 3, ['pred_F'], {INPUTS}).map(s => [s.text, s.kind])"),
    "suggest_functions": f"ml.suggestions('no', 2, [], {INPUTS}).map(s => [s.text, s.kind])",
    "suggest_skips_what_is_typed": f"ml.suggestions('norm', 4, [], {INPUTS}).map(s => s.text)",
    "suggest_input_carries_its_ref": f"ml.suggestions('ref_', 4, [], {INPUTS}).map(s => [s.text, s.ref])",

    # ── the inputs list ─────────────────────────────────────────────────────
    "groups_in_order": f"ml.groupInputs({INPUTS}).map(g => [g.group, g.items.length])",
    "groups_filtered": f"ml.groupInputs({INPUTS}, 'energies').map(g => [g.group, g.items.map(i => i.ref)])",
    "groups_filter_matches_ref": f"ml.groupInputs({INPUTS}, 'info.ref_').map(g => g.group)",
    "side_marks": f"{INPUTS}.map(ml.sideMark)",

    # ── ids ─────────────────────────────────────────────────────────────────
    "id_problems": (
        "['', 'user.', 'nodot', 'user.a b', 'user.ok', 'mylab.e_per_atom', 'user.taken']"
        ".map(i => ml.idProblem(i, ['user.taken']))"),

    # ── what saves ──────────────────────────────────────────────────────────
    "payload_drops_unused_vars": (
        "ml.toPayload({id: 'user.a', label: '', unit: '', expr: 'a + 1',"
        " vars: [{name: 'a', ref: 'r.a'}, {name: 'b', ref: 'r.b'}]})"),
    "draft_round_trip": (
        "ml.toPayload(ml.toDraft({id: 'user.a', label: 'L', unit: 'u', expr: 'a', vars: {a: 'r.a'}}))"),
    "dirty_same": (
        "ml.isDirty(ml.toDraft({id: 'user.a', label: 'L', unit: 'u', expr: 'a', vars: {a: 'r.a'}}),"
        " {id: 'user.a', label: 'L', unit: 'u', expr: 'a', vars: {a: 'r.a'}})"),
    "dirty_after_edit": (
        "ml.isDirty({id: 'user.a', label: 'L', unit: 'u', expr: 'a * 2', vars: [{name: 'a', ref: 'r.a'}]},"
        " {id: 'user.a', label: 'L', unit: 'u', expr: 'a', vars: {a: 'r.a'}})"),
    "dirty_new_empty_and_filled": (
        "[ml.isDirty({id: 'user.', label: '', unit: '', expr: '', vars: []}, null),"
        " ml.isDirty({id: 'user.', label: '', unit: '', expr: 'a', vars: []}, null)]"),

    # ── shortcuts and words ─────────────────────────────────────────────────
    "shortcuts": "['N_atoms', 'N_frames', 'scalar', '(N_atoms, xyz)', null].map(ml.shortcutFor)",
    "shape_hints": "['N_frames', 'N_atoms', 'scalar'].map(ml.shapeHint)",
    "colour_refused": (
        "[ml.shapeHint('N_atoms', 'by=all can\\'t be used for colouring yet: colouring sees one frame'),"
        " ml.shortcutFor('N_atoms', 'by=all ...'), ml.shortcutFor('N_atoms', null), ml.shapeHint('N_elements')]"),
    "stats_format": "[ml.formatStat(null), ml.formatStat(NaN), ml.formatStat(0), ml.formatStat(12.3456789), ml.formatStat(1234567), ml.formatStat(0.00012)]",
    "try_headlines": (
        "[ml.tryHeadline({ok: true, count: 4, bad_count: 0}), ml.tryHeadline({ok: true, count: 4, bad_count: 1}),"
        " ml.tryHeadline({ok: true, count: 1, bad_count: 0}), ml.tryHeadline({ok: false, error: 'boom'}),"
        " ml.tryHeadline({ok: true, cancelled: true})]"),
    "try_headlines_named": (
        "[ml.tryHeadline({ok: true, count: 12, bad_count: 0}, 'aspirin · MACE'),"
        " ml.tryHeadline({ok: true, count: 4, bad_count: 1}, 'aspirin'),"
        " ml.tryHeadline({ok: false, error: 'boom'}, 'aspirin'), ml.tryHeadline({ok: true, cancelled: true}, 'aspirin'),"
        " ml.tryHeadline({ok: true, count: 1, bad_count: 0}, '')]"),

    "no_value_lines": (
        "[ml.noValueLine({no_value_count: 0, no_value_rows: 'structures'}),"
        " ml.noValueLine({no_value_count: 12, no_value_rows: 'structures'}),"
        " ml.noValueLine({no_value_count: 1, no_value_rows: 'structures'}),"
        " ml.noValueLine({no_value_count: 2, no_value_rows: 'elements'}),"
        " ml.noValueLine({no_value_count: 1, no_value_rows: 'result'}), ml.noValueLine({})]"),

    # ── Plot it (rule 13) ───────────────────────────────────────────────────
    "plot_panel": "te.metricPanel({id: 'user.a', label: 'A'})",
    "plot_makes_my_metrics_with_the_main_view": (
        "(() => { const r = te.plotTarget([{name: 'Basic', panels: [{kind: 'table', row: 0, col: 0}]}],"
        " te.metricPanel({id: 'user.a', label: 'A'}));"
        "  return [r.previous, r.tab.name, r.tab.panels.map(p => [p.kind, p.title, p.row, p.col])]; })()"),
    "plot_reuses_an_existing_my_metrics": (
        "(() => { const mine = {name: 'My metrics', panels: [{kind: 'timeline', title: 'B', row: 0, col: 0, metrics: {}}]};"
        "  const r = te.plotTarget([{name: '3D', panels: [{kind: '3d'}]}, mine], te.metricPanel({id: 'user.a', label: 'A'}));"
        "  return [r.previous, r.tab.panels.map(p => [p.title, p.row, p.col])]; })()"),

    # ── the question after a session load (rule 15) ─────────────────────────
    "session_question_one": "ml.sessionMetricsQuestion([{id: 'user.a', clash_with: null}])",
    "session_question_clash": (
        "ml.sessionMetricsQuestion([{id: 'user.a', clash_with: null}, {id: 'user.b.2', clash_with: 'user.b'}]).message"),

    # ── the list by origin ──────────────────────────────────────────────────
    "group_metrics": (
        "(() => { const g = ml.groupMetrics("
        "  [{id: 'ffast.b', label: 'B', origin: 'built-in', shape: 'N_frames'},"
        "   {id: 'proj.p', label: 'P', origin: 'project', shape: 'N_atoms', expr: 'x', vars: {x: 'r'}},"
        "   {id: 'user.u', label: '', origin: 'user', shape: 'N_frames'},"
        "   {id: 'user.u__kde__p1', label: 'derived', origin: 'user', shape: 'x'}],"
        "  [{id: 'user.u', label: 'Mine', expr: 'a', vars: {a: 'r'}, status: 'ok', revision: 'rev1', shape: 'N_frames', used_by: []},"
        "   {id: 'user.h', label: '', expr: 'a', vars: {}, status: 'hidden', message: 'Hidden: x'}]);"
        "  return Object.fromEntries(Object.entries(g).map(([k, v]) => [k, v.map(m => [m.id, m.label, m.status, m.revision])])); })()"),
    "group_metrics_used_by": (
        "(() => { const g = ml.groupMetrics("
        "  [{id: 'ffast.b', label: 'B', origin: 'built-in', shape: 'N_frames'},"
        "   {id: 'ffast.c', label: 'C', origin: 'built-in', shape: 'N_frames'},"
        "   {id: 'user.u', label: 'Mine', origin: 'user', shape: 'N_frames'}],"
        "  [{id: 'user.u', label: 'Mine', status: 'ok', shape: 'N_frames'}],"
        "  {'ffast.b': ['tab Basic, panel MAE'], 'user.u': ['metric user.v']});"
        "  return Object.fromEntries([...g['built-in'], ...g.user].map(m => [m.id, m.used_by])); })()"),
}


@pytest.fixture(scope="module")
def results():
    playwright_api = pytest.importorskip("playwright.async_api")
    handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(STATIC_DIR))
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    origin = f"http://127.0.0.1:{server.server_address[1]}"
    script = (
        "async () => {\n"
        f"  const ml = await import('{origin}/metric_logic.js');\n"
        f"  const te = await import('{origin}/tab_edit.js');\n"
        "  const out = {};\n"
        + "".join(f"  out[{json.dumps(n)}] = {e};\n" for n, e in CASES.items())
        + "  return out;\n}"
    )

    async def _collect():
        async with playwright_api.async_playwright() as p:
            browser = await p.chromium.launch()
            page = await browser.new_page()
            errors: list[str] = []
            page.on("pageerror", lambda exc: errors.append(str(exc)))
            await page.goto(f"{origin}/events.js")
            await page.set_content("<!doctype html><title>helpers</title>")
            try:
                out = await page.evaluate(script)
            finally:
                await browser.close()
            assert not errors, f"page errors: {errors}"
            return out

    try:
        yield asyncio.run(_collect())
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


# ── names ───────────────────────────────────────────────────────────────────
def test_a_function_name_is_not_a_value(results):
    assert results["names_skip_functions"] == ["pred_F", "ref_F", "n_atoms"]


def test_x_of_F_uses_F_not_x(results):
    assert results["names_x_call_is_not_a_value"] == ["F"]


def test_a_variable_may_be_called_x(results):
    assert results["names_a_variable_called_x"] == ["x"]


def test_the_e_of_an_exponent_is_not_a_name(results):
    assert results["names_numbers_with_exponents"] == ["a"]


def test_pi_and_n_atoms_need_no_variable(results):
    assert results["undefined_ignores_constants"] == []
    assert results["undefined_lists_unknown"] == ["b", "c"]


# ── Fix ─────────────────────────────────────────────────────────────────────
def test_fix_finds_the_input_a_short_name_means(results):
    assert results["fix_by_short_name"] == "reference.energies"
    assert results["fix_ignores_case"] == "reference.energies"


def test_fix_falls_back_to_a_unique_label(results):
    assert results["fix_by_label_when_unique"] == "reference.positions"


def test_fix_is_offered_only_when_exactly_one_input_matches(results):
    assert results["fix_none_when_several_match"] is None     # energies: reference and prediction
    assert results["fix_none_when_nothing_matches"] is None


# ── editing ─────────────────────────────────────────────────────────────────
def test_unique_name_numbers_from_two(results):
    assert results["unique_name"] == ["a", "a_2", "a_3"]


def test_insert_replaces_the_selection_and_moves_the_caret_after_it(results):
    assert results["insert_replaces_selection"] == {"text": "pred_E + d", "caret": 6}
    assert results["insert_at_caret"] == {"text": "a + b", "caret": 5}


def test_completing_replaces_only_the_typed_part(results):
    assert results["complete_replaces_the_typed_part"] == {"text": "2 * pred_E", "caret": 10}


def test_renaming_changes_values_not_function_calls(results):
    assert results["rename_changes_values_only"] == "x(F) + F * k"
    assert results["rename_leaves_words_and_text"] == 'mean(v, by=all, where=element("all"))'


def test_the_editor_reads_reductions(results):
    """ADR 0064: by=/where= are keywords, the word after by= is a word, the
    symbols in element(...) are text; none of them is an input."""
    assert results["names_skip_by_words_and_symbols"] == ["pF", "rF", "e"]
    assert results["names_keep_a_comparison_and_a_variable_called_all"] == ["a", "all"]
    assert results["suggest_by_words"] == [[["structure", "word"]], 0, ["mean", "mean_parts"]]
    assert results["rename_whole_names_only"] == "ab + q + abc"


def test_variable_names_must_be_clean_and_new(results):
    assert results["variable_name_problems"] == [
        "A name is letters, digits and underscores, not starting with a digit",
        "pi is built in",
        "A name is letters, digits and underscores, not starting with a digit",
        "", "dup is already a variable"]


# ── suggestions ─────────────────────────────────────────────────────────────
def test_nothing_is_suggested_without_a_typed_prefix(results):
    assert results["suggest_nothing_without_a_prefix"] == []


def test_suggestions_run_variables_then_inputs_then_functions(results):
    # "pre" matches the variable pred_F and the input pred_E; no function starts with it
    assert results["suggest_vars_then_inputs_then_functions"] == [["pred_F", "variable"], ["pred_E", "input"]]
    assert results["suggest_functions"] == [["norm", "function"]]


def test_a_name_already_typed_in_full_is_not_suggested_back(results):
    assert results["suggest_skips_what_is_typed"] == []


def test_an_input_suggestion_carries_the_ref_to_bind(results):
    assert results["suggest_input_carries_its_ref"] == [
        ["ref_E", "reference.energies"], ["ref_R", "reference.positions"],
        ["ref_REF_energy", "reference.info.REF_energy"]]


# ── inputs list ─────────────────────────────────────────────────────────────
def test_inputs_are_grouped_in_the_documented_order(results):
    assert results["groups_in_order"] == [["Reference", 2], ["Prediction", 1], ["File keys", 1], ["Metrics", 1]]


def test_the_search_matches_ref_name_and_label(results):
    assert results["groups_filtered"] == [
        ["Reference", ["reference.energies"]], ["Prediction", ["prediction.energies"]]]
    assert results["groups_filter_matches_ref"] == ["File keys"]


def test_one_sided_inputs_are_marked(results):
    assert results["side_marks"] == ["", "", "reference only", "", ""]


# ── ids ─────────────────────────────────────────────────────────────────────
def test_id_problems_are_named(results):
    problems = results["id_problems"]
    assert problems[0].startswith("Type an id") and problems[1].startswith("Type an id")
    assert problems[2].startswith("An id is letters") and problems[3].startswith("An id is letters")
    assert problems[4] == "" and problems[5] == ""
    assert problems[6] == "The id user.taken is already taken"


# ── payload and dirtiness ───────────────────────────────────────────────────
def test_unused_variables_are_not_saved(results):
    assert results["payload_drops_unused_vars"] == {
        "id": "user.a", "expr": "a + 1", "vars": {"a": "r.a"}, "label": "", "unit": ""}


def test_a_loaded_metric_is_not_dirty_until_edited(results):
    assert results["draft_round_trip"]["vars"] == {"a": "r.a"}
    assert results["dirty_same"] is False
    assert results["dirty_after_edit"] is True
    assert results["dirty_new_empty_and_filled"] == [False, True]


# ── shortcuts, words ────────────────────────────────────────────────────────
def test_the_shortcut_follows_the_result_shape(results):
    assert results["shortcuts"] == ["colour", "plot", None, None, None]


def test_the_check_says_where_a_result_can_go(results):
    assert results["shape_hints"] == [
        "per structure → can be plotted", "per atom → can colour atoms", "one number for the dataset"]


def test_numbers_are_short(results):
    assert results["stats_format"] == ["—", "—", "0", "12.3457", "1.235e+6", "1.200e-4"]


def test_a_try_opens_with_one_plain_line(results):
    assert results["try_headlines"] == [
        "4 values, all good", "1 bad value of 4 — saving would fail the metric",
        "1 value, all good", "boom", "Stopped"]


def test_a_try_names_the_data_it_ran_on(results):
    assert results["try_headlines_named"] == [
        "12 values, all good — on aspirin · MACE",
        "1 bad value of 4 — saving would fail the metric — on aspirin",
        "boom", "Stopped", "1 value, all good"]


def test_try_says_how_many_places_have_no_value(results):
    assert results["no_value_lines"] == [
        "", "12 structures have no value", "1 structure has no value",
        "2 elements have no value", "The result has no value", ""]


def test_the_list_is_grouped_by_origin_with_user_entries_from_the_user_list(results):
    g = results["group_metrics"]
    assert g["built-in"] == [["ffast.b", "B", "ok", None]]
    assert g["project"] == [["proj.p", "P", "ok", None]]
    # the derived transform is left out; sorted by label, the hidden file by its id
    assert g["user"] == [["user.u", "Mine", "ok", "rev1"], ["user.h", "user.h", "hidden", None]]


def test_used_by_comes_from_the_users_map(results):
    assert results["group_metrics_used_by"] == {
        "ffast.b": ["tab Basic, panel MAE"], "ffast.c": [], "user.u": ["metric user.v"]}


# ── Plot it ─────────────────────────────────────────────────────────────────
def test_plot_it_adds_a_timeline_of_the_metric(results):
    panel = results["plot_panel"]
    assert (panel["kind"], panel["title"], panel["metrics"]) == ("timeline", "A", {"y": "user.a"})
    assert panel["metric_refs"] == {"y": {"metric": "user.a", "transform": None, "params": {}}}


def test_plot_it_always_makes_or_uses_my_metrics_never_another_tab(results):
    # a new My metrics tab holds the main view too, so the pane stays beside the plot
    assert results["plot_makes_my_metrics_with_the_main_view"] == [
        None, "My metrics", [["3d", None, 0, 0], ["timeline", "A", 0, 1]]]


def test_plot_it_adds_to_my_metrics_when_it_exists(results):
    assert results["plot_reuses_an_existing_my_metrics"] == ["My metrics", [["B", 0, 0], ["A", 1, 0]]]


# ── sessions ────────────────────────────────────────────────────────────────
def test_the_session_question_counts_the_metrics(results):
    q = results["session_question_one"]
    assert q["title"] == "Metrics in this session"
    assert q["message"] == "This session uses 1 metric this server doesn't have:\n• user.a\n\nAdd them?"


def test_the_session_question_says_how_a_clash_is_kept(results):
    assert results["session_question_clash"] == (
        "This session uses 2 metrics this server doesn't have:\n• user.a\n"
        "• user.b.2 — the server has a different user.b; the session's is kept as user.b.2\n\nAdd them?")


def test_duplicating_a_formula_metric_copies_its_formula(results):
    assert results["dup_formula"] == {"id": "p.e", "label": "E", "unit": "energy", "expr": "2 * e",
                                      "vars": [{"name": "e", "ref": "reference.energies"}]}


def test_duplicating_a_python_metric_with_an_exact_formula_copies_that_formula(results):
    d = results["dup_equivalent"]
    assert d["expr"] == "norm(pred_F - ref_F)" and d["unit"] == "force"
    assert d["vars"] == [{"name": "pred_F", "ref": "prediction.forces"},
                         {"name": "ref_F", "ref": "reference.forces"}]


def test_duplicating_any_other_python_metric_takes_it_as_the_input(results):
    d = results["dup_wrapper"]
    assert d["expr"] == "force_mae_per_structure"
    assert d["vars"] == [{"name": "force_mae_per_structure", "ref": "ffast.force_mae_per_structure"}]
    assert results["dup_wrapper_not_a_function"] == "m_x"   # x(F) is a function


def test_a_formula_colouring_refuses_says_why_and_offers_no_colour_shortcut(results):
    assert results["colour_refused"] == [
        "per atom → by=all can't be used for colouring yet: colouring sees one frame",
        None, "colour", "per element"]
