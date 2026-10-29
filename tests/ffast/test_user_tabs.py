"""User tabs and the three tab sources (ADR 0056 rules 9, 10, 14, 16).

A user tab is one TOML file in ~/.ffast/tabs/ on the server's machine, in the
same format as a built-in tab. Which built-in or project tab it replaces, the
original's fingerprint at edit time, the hidden list and the order of new tabs
live in a side file, state.json, so a tab file can be copied into a project
as it is. These tests point the store at a temporary folder.
"""
import json
import tomllib

import pytest

import ffast.metrics.builtin  # noqa: F401  (registers the metrics the tabs bind)
from ffast.config.models import AnalysisTabConfig, ProjectConfig
from ffast.config.user_tabs import TabError, TabSources, UserTabStore


def _tab(name, *panels):
    return AnalysisTabConfig.model_validate({"name": name, "panels": list(panels)})


VIEW = {"kind": "3d", "row": 0, "col": 0}
TABLE = {"kind": "table", "row": 0, "col": 1, "title": "MAE",
         "metrics": {"value": {"metric": "ffast.force_difference[dataset;component;mae]"}}}
SMOOTH = {"kind": "timeline", "row": 0, "col": 0, "title": "Gyradius",
          "metrics": {"y": {"metric": "ffast.gyradius", "transform": "smooth"}}}


@pytest.fixture
def store(tmp_path):
    return UserTabStore(tmp_path / "tabs")


def _sources(store, builtin=None, project=()):
    builtin = builtin if builtin is not None else [
        _tab("3D", VIEW), _tab("Basic", TABLE), _tab("Gyration", SMOOTH)]
    sources = TabSources(builtin=builtin, project=list(project), store=store)
    assert sources.compile() == []   # as the server does at startup
    return sources


def _names(sources, *, visible=False):
    return [e.tab.name for e in sources.entries() if not (visible and e.hidden)]


# ── saving and reading ──────────────────────────────────────────────────────

def test_a_new_tab_is_one_toml_file_in_the_shared_format(store, tmp_path):
    sources = _sources(store)
    sources.save({"name": "Compare", "panels": [VIEW, TABLE]})
    path = tmp_path / "tabs" / "compare.toml"
    data = tomllib.loads(path.read_text())
    assert [t["name"] for t in data["tabs"]] == ["Compare"]
    assert AnalysisTabConfig.model_validate(data["tabs"][0]).panels[1].title == "MAE"
    assert "replaces" not in path.read_text()


def test_sizes_are_written_as_a_person_would(store, tmp_path):
    _sources(store).save({"name": "Sized", "panels": [VIEW], "column_widths": [2.0, 1.5]})
    data = tomllib.loads((tmp_path / "tabs" / "sized.toml").read_text())
    assert data["tabs"][0]["column_widths"] == [2, 1.5]
    assert isinstance(data["tabs"][0]["column_widths"][0], int)


def test_new_user_tabs_follow_the_others_in_the_order_they_were_made(store):
    project = [_tab("Mine", TABLE)]
    sources = _sources(store, project=project)
    sources.save({"name": "Zeta", "panels": [TABLE]})
    sources.save({"name": "Alpha", "panels": [TABLE]})
    assert _names(sources) == ["3D", "Basic", "Gyration", "Mine", "Zeta", "Alpha"]
    sources = _sources(UserTabStore(store.root), project=project)   # a fresh read
    assert _names(sources) == ["3D", "Basic", "Gyration", "Mine", "Zeta", "Alpha"]
    assert [e.source for e in sources.entries()] == [
        "built-in", "built-in", "built-in", "project", "user", "user"]


def test_editing_a_built_in_tab_saves_a_user_tab_in_its_place(store):
    sources = _sources(store)
    sources.save({"name": "Basic", "panels": [TABLE, {**TABLE, "row": 1}]}, previous_name="Basic")
    entry = next(e for e in sources.entries() if e.tab.name == "Basic")
    assert _names(sources) == ["3D", "Basic", "Gyration"]
    assert (entry.source, entry.replaces, entry.original_changed) == ("user", "Basic", False)
    assert len(entry.tab.panels) == 2


def test_an_edit_may_rename_the_tab_it_replaces(store):
    sources = _sources(store)
    sources.save({"name": "Basic v2", "panels": [TABLE]}, previous_name="Basic")
    assert _names(sources) == ["3D", "Basic v2", "Gyration"]
    sources.save({"name": "Basic v3", "panels": [TABLE]}, previous_name="Basic v2")
    entry = next(e for e in sources.entries() if e.tab.name == "Basic v3")
    assert entry.replaces == "Basic"
    assert len(list(store.root.glob("*.toml"))) == 1   # the same file, rewritten


def test_reset_to_original_deletes_the_user_tab(store):
    sources = _sources(store)
    sources.save({"name": "Basic", "panels": [TABLE, {**TABLE, "row": 1}]}, previous_name="Basic")
    sources.delete("Basic")
    entry = next(e for e in sources.entries() if e.tab.name == "Basic")
    assert entry.source == "built-in" and len(entry.tab.panels) == 1
    assert list(store.root.glob("*.toml")) == []


def test_the_mark_says_when_the_original_changed_since_the_edit(store):
    _sources(store).save({"name": "Basic", "panels": [TABLE]}, previous_name="Basic")
    changed = [_tab("3D", VIEW), _tab("Basic", {**TABLE, "title": "MAE (new)"})]
    entry = next(e for e in _sources(store, builtin=changed).entries() if e.tab.name == "Basic")
    assert entry.replaces == "Basic" and entry.original_changed is True


def test_a_name_another_tab_has_is_refused(store):
    sources = _sources(store)
    with pytest.raises(TabError, match="A tab named Gyration already exists"):
        sources.save({"name": "Gyration", "panels": [TABLE]})
    with pytest.raises(TabError, match="A tab named 3D already exists"):
        sources.save({"name": "3D", "panels": [TABLE]}, previous_name="Basic")


def test_a_tab_the_config_rules_reject_is_refused(store):
    with pytest.raises(TabError, match="column_widths"):
        _sources(store).save({"name": "Bad", "panels": [TABLE], "column_widths": [1]})


def test_a_tab_needing_a_metric_the_server_lacks_is_refused(store):
    """A running server's metric graph is fixed (rule 11): a tab may only use
    metrics it already has."""
    with pytest.raises(TabError, match="nope.metric"):
        _sources(store).save({"name": "Bad", "panels": [
            {"kind": "table", "row": 0, "col": 0, "metrics": {"value": {"metric": "nope.metric"}}}]})


def test_only_user_tabs_can_be_deleted(store):
    with pytest.raises(TabError, match="Basic is a built-in tab"):
        _sources(store).delete("Basic")


# ── the main view always has a place (rule 6) ───────────────────────────────

def test_a_save_that_removes_the_last_linked_3d_panel_is_refused(store):
    with pytest.raises(TabError, match="At least one tab must show the main view"):
        _sources(store).save({"name": "3D", "panels": [TABLE]}, previous_name="3D")


def test_the_last_tab_showing_the_main_view_cannot_be_hidden(store):
    with pytest.raises(TabError, match="At least one tab must show the main view"):
        _sources(store).set_hidden("3D", True)


# ── hiding ──────────────────────────────────────────────────────────────────

def test_any_tab_can_be_hidden_and_shown_again(store):
    sources = _sources(store)
    sources.set_hidden("Gyration", True)
    assert _names(sources, visible=True) == ["3D", "Basic"]
    assert json.loads((store.root / "state.json").read_text())["hidden"] == ["Gyration"]
    assert _names(_sources(UserTabStore(store.root)), visible=True) == ["3D", "Basic"]
    sources.set_hidden("Gyration", False)
    assert _names(sources, visible=True) == ["3D", "Basic", "Gyration"]


def test_deleting_a_hidden_user_tab_drops_it_from_the_hidden_list(store):
    sources = _sources(store)
    sources.save({"name": "Compare", "panels": [TABLE]})
    sources.set_hidden("Compare", True)
    sources.delete("Compare")
    assert json.loads((store.root / "state.json").read_text())["hidden"] == []


# ── export, layout, broken files ────────────────────────────────────────────

def test_export_gives_a_project_tab_to_paste_into_ffast_toml(store):
    sources = _sources(store)
    sources.save({"name": "Basic", "panels": [SMOOTH]}, previous_name="Basic")
    text = sources.export("Basic")
    project = ProjectConfig.model_validate(tomllib.loads(text))
    assert [t.name for t in project.visualization.tabs] == ["Basic"]
    assert project.visualization.tabs[0].panels[0].metrics["y"].transform == "smooth"
    assert "replaces" not in text and "state" not in text


def test_an_exported_tab_dropped_into_the_folder_loads_as_a_user_tab(store):
    store.root.mkdir(parents=True)
    (store.root / "shared.toml").write_text(_sources(store).export("Gyration").replace(
        'name = "Gyration"', 'name = "Shared"'))
    assert _names(_sources(UserTabStore(store.root)))[-1] == "Shared"


def test_a_broken_tab_file_is_skipped_and_reported(store):
    store.root.mkdir(parents=True)
    (store.root / "broken.toml").write_text("[[tabs\n")
    (store.root / "good.toml").write_text('[[tabs]]\nname = "Good"\n')
    sources = _sources(UserTabStore(store.root))
    assert _names(sources)[-1] == "Good"
    assert [file for file, _ in sources.store.errors] == ["broken.toml"]


def test_the_layout_tells_the_browser_where_each_tab_comes_from(store):
    sources = _sources(store)
    sources.save({"name": "Basic", "panels": [SMOOTH]}, previous_name="Basic")
    sources.set_hidden("Gyration", True)
    layout = {t["name"]: t for t in sources.layout()}
    basic, gyration = layout["Basic"], layout["Gyration"]
    assert (basic["source"], basic["replaces"], basic["original_changed"]) == ("user", "Basic", False)
    assert (gyration["source"], gyration["hidden"]) == ("built-in", True)
    assert basic["revision"] and basic["revision"] != layout["3D"]["revision"]
    panel = basic["panels"][0]
    assert panel["metrics"]["y"] == "ffast.gyradius__smooth"             # what to fetch
    assert panel["metric_refs"]["y"] == {"metric": "ffast.gyradius", "transform": "smooth", "params": {}}


# ── independent 3D panels (rule 13) ─────────────────────────────────────────

INDEPENDENT = {"kind": "3d", "row": 0, "col": 1, "view": "independent", "link_camera": False,
               "start": {"colour_by": "ffast.force_difference[atom+structure;length]", "colormap": "force_error"}}


def test_an_independent_panel_is_saved_with_its_links_and_start(store, tmp_path):
    _sources(store).save({"name": "Compare", "panels": [VIEW, INDEPENDENT]})
    data = tomllib.loads((tmp_path / "tabs" / "compare.toml").read_text())
    linked, independent = data["tabs"][0]["panels"]
    assert linked == VIEW
    assert independent == INDEPENDENT


def test_making_the_last_linked_panel_independent_is_refused(store):
    with pytest.raises(TabError, match="At least one tab must show the main view"):
        _sources(store).save({"name": "3D", "panels": [{**VIEW, "view": "independent"}]},
                             previous_name="3D")


# ── a missing metric is reported where it was used (ADR 0057 rule 15) ───────

def test_a_panel_with_a_missing_metric_does_not_take_its_tab_down():
    from ffast.config.tabs import tab_layout
    gone = {"kind": "timeline", "row": 0, "col": 1, "title": "Gone",
            "metrics": {"y": {"metric": "mine.not_here", "transform": "smooth"}}}
    layout = tab_layout(_tab("Mixed", SMOOTH, gone))
    good, broken = layout["panels"]
    assert good["unresolved"] == []
    assert good["metrics"]["y"] == "ffast.gyradius__smooth"
    assert broken["unresolved"] == ["mine.not_here"]
    assert broken["metrics"]["y"] == "mine.not_here"


# ── tabs are kept until the folder changes (ADR 0062 decision 5) ─────────────

@pytest.fixture
def reads(monkeypatch):
    """How many tab files the store parsed."""
    calls = []
    original = UserTabStore._read

    def counting(path):
        calls.append(path.name)
        return original(path)

    monkeypatch.setattr(UserTabStore, "_read", staticmethod(counting))
    return calls


def test_an_unchanged_folder_is_parsed_once(store, reads):
    store.write(_tab("One", TABLE))
    store.write(_tab("Two", TABLE))
    store.tabs()
    reads.clear()
    first, second = store.tabs(), store.tabs()
    assert reads == []
    assert [t.tab.name for t in second] == ["One", "Two"]
    assert first is not second   # a caller can't change the kept list


def test_errors_are_kept_with_the_tabs(store, reads):
    store.root.mkdir(parents=True)
    (store.root / "broken.toml").write_text("not a tab")
    store.tabs()
    store.errors = []
    store.tabs()
    assert [f for f, _ in store.errors] == ["broken.toml"]


def test_a_tab_file_copied_in_by_hand_appears_at_the_next_call(store, tmp_path, reads):
    store.write(_tab("One", TABLE))
    assert len(store.tabs()) == 1
    src = store.root / "one.toml"
    (store.root / "copy.toml").write_text(src.read_text().replace("One", "Copy"))
    assert [t.tab.name for t in store.tabs()] == ["One", "Copy"]


def test_a_file_edited_by_hand_is_read_again(store, reads):
    store.write(_tab("One", TABLE))
    store.tabs()
    path = store.root / "one.toml"
    path.write_text(path.read_text().replace("MAE", "Mean error"))
    assert store.tabs()[0].tab.panels[0].title == "Mean error"


def test_write_delete_and_hide_are_seen_at_the_next_call(store):
    file = store.write(_tab("One", TABLE))
    assert [t.tab.name for t in store.tabs()] == ["One"]
    store.write(_tab("Two", TABLE))
    assert [t.tab.name for t in store.tabs()] == ["One", "Two"]
    store.set_hidden("One", True)
    assert store.hidden == ["One"]
    store.delete(file, "One")
    assert [t.tab.name for t in store.tabs()] == ["Two"]


# ── order (ADR 0070) ────────────────────────────────────────────────────────

def test_a_set_order_is_the_bar_order_and_is_kept_in_state_json(store):
    sources = _sources(store)
    sources.set_order(["Gyration", "3D", "Basic"])
    assert _names(sources) == ["Gyration", "3D", "Basic"]
    assert _names(_sources(UserTabStore(store.root))) == ["Gyration", "3D", "Basic"]


def test_tabs_not_in_the_order_go_after_it(store):
    sources = _sources(store)
    sources.set_order(["Basic", "3D"])
    assert _names(sources) == ["Basic", "3D", "Gyration"]
    sources.save({"name": "Mine", "panels": [VIEW]})
    assert _names(sources)[-1] == "Mine"


def test_an_empty_order_brings_back_the_default(store):
    sources = _sources(store)
    sources.set_order(["Gyration", "Basic", "3D"])
    sources.set_order([])
    assert _names(sources) == ["3D", "Basic", "Gyration"]


def test_unknown_names_are_dropped_and_a_repeated_name_is_refused(store):
    sources = _sources(store)
    sources.set_order(["Gone", "Basic", "3D"])
    assert store.bar_order == ["Basic", "3D"]
    with pytest.raises(TabError, match="listed twice"):
        sources.set_order(["Basic", "Basic"])


def test_a_renamed_tab_keeps_its_place(store):
    sources = _sources(store)
    sources.save({"name": "Mine", "panels": [VIEW]})
    sources.set_order(["Mine", "3D", "Basic", "Gyration"])
    sources.save({"name": "Ours", "panels": [VIEW]}, previous_name="Mine")
    assert _names(sources) == ["Ours", "3D", "Basic", "Gyration"]


def test_reset_to_original_gives_the_place_back(store):
    sources = _sources(store)
    sources.save({"name": "Basic 2", "panels": [TABLE]}, previous_name="Basic")
    sources.set_order(["Basic 2", "3D", "Gyration"])
    sources.delete("Basic 2")
    assert _names(sources) == ["Basic", "3D", "Gyration"]


def test_deleting_a_tab_drops_it_from_the_order(store):
    sources = _sources(store)
    sources.save({"name": "Mine", "panels": [VIEW]})
    sources.set_order(["Mine", "3D"])
    sources.delete("Mine")
    assert store.bar_order == ["3D"]


def test_a_hidden_tab_keeps_its_place(store):
    sources = _sources(store)
    sources.set_order(["Gyration", "Basic", "3D"])
    sources.set_hidden("Basic", True)
    sources.set_hidden("Basic", False)
    assert _names(sources) == ["Gyration", "Basic", "3D"]


def test_hiding_and_ordering_do_not_wipe_each_other(store):
    sources = _sources(store)
    sources.set_order(["Basic", "3D"])
    sources.set_hidden("Gyration", True)
    state = json.loads((store.root / "state.json").read_text())
    assert state["bar_order"] == ["Basic", "3D"] and state["hidden"] == ["Gyration"]
