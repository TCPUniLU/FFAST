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
         "metrics": {"value": {"metric": "ffast.force_component_mae"}}}
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
