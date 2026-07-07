"""Unit tests for the plugin/module loader (``utils.loadModules``) and its
dependency-ordering primitives (``kahnsAlgorithm`` /
``checkForInvalidDependencies``).

The plugin-discovery system globs ``modules/**/*.py``, reads each plugin's
``DEPENDENCIES`` list, and loads them in topological order so a plugin's
dependencies have run ``loadData`` before it does. These tests cover both the
pure ordering functions and ``loadModules`` end-to-end against fake plugin
files laid out in ``tmp_path`` (with the discovery glob monkeypatched to point
at them). ``NOTE``: this file is distinct from ``test_module_loader.py``, which
covers the unrelated metric-config module loader (``load_metric_modules``).
"""
import logging
import types

import utils
from utils import checkForInvalidDependencies, kahnsAlgorithm, loadModules


# ── pure ordering unit: kahnsAlgorithm ────────────────────────────────────

def test_kahns_algorithm_orders_linear_chain_dependencies_first():
    # graph maps node -> its dependencies. c depends on b, b depends on a, so a
    # must load first, then b, then c.
    # ARRANGE
    graph = {"a": [], "b": ["a"], "c": ["b"]}

    # ACT
    order, degreeMap = kahnsAlgorithm(graph)

    # ASSERT — dependency-first topological order
    assert order == ["a", "b", "c"]


def test_kahns_algorithm_orders_diamond_dependencies_before_dependents():
    # d depends on b and c; both depend on a. a must precede b and c, which must
    # both precede d.
    # ARRANGE
    graph = {"a": [], "b": ["a"], "c": ["a"], "d": ["b", "c"]}

    # ACT
    order, _ = kahnsAlgorithm(graph)

    # ASSERT
    assert order[0] == "a"
    assert order[-1] == "d"
    assert order.index("b") < order.index("d")
    assert order.index("c") < order.index("d")


def test_kahns_algorithm_returns_none_on_cycle():
    # a <-> b mutual dependency: no valid ordering exists.
    # ARRANGE
    graph = {"a": ["b"], "b": ["a"]}

    # ACT
    order, degreeMap = kahnsAlgorithm(graph)

    # ASSERT — None signals "cycle"; both nodes keep a non-zero in-degree because
    # neither could ever be scheduled.
    assert order is None
    assert degreeMap == {"a": 1, "b": 1}


# ── pure ordering unit: checkForInvalidDependencies ───────────────────────

def test_check_invalid_dependencies_drops_node_with_missing_dependency():
    # ARRANGE — "a" depends on a plugin that was never discovered.
    graph = {"a": ["ghost"]}

    # ACT
    cleaned = checkForInvalidDependencies(graph)

    # ASSERT
    assert cleaned == {}


def test_check_invalid_dependencies_cascades_removal():
    # b depends on a missing plugin, so b is dropped; a depends on b, so once b
    # is gone a becomes invalid too and is cascaded out.
    # ARRANGE
    graph = {"a": ["b"], "b": ["ghost"]}

    # ACT
    cleaned = checkForInvalidDependencies(graph)

    # ASSERT
    assert cleaned == {}


def test_check_invalid_dependencies_keeps_valid_graph_unchanged():
    # ARRANGE — every dependency resolves within the graph.
    graph = {"a": [], "b": ["a"]}

    # ACT
    cleaned = checkForInvalidDependencies(graph)

    # ASSERT
    assert cleaned == {"a": [], "b": ["a"]}


# ── end-to-end: loadModules over fake plugin files ────────────────────────

def _write_plugin(dir_path, name, dependencies):
    """Write a minimal plugin file whose ``loadData`` records its own name."""
    body = (
        f"DEPENDENCIES = {dependencies!r}\n"
        "def loadData(env):\n"
        f"    env.loaded.append({name!r})\n"
    )
    path = dir_path / f"{name}.py"
    path.write_text(body)
    return str(path)


def test_load_modules_calls_loadData_in_topological_order(tmp_path, monkeypatch):
    # ARRANGE — linear chain a <- b <- c, listed to the glob in a deliberately
    # non-topological (reverse) order so the ordering can't be an accident of
    # discovery order.
    paths = [
        _write_plugin(tmp_path, "c", ["b"]),
        _write_plugin(tmp_path, "b", ["a"]),
        _write_plugin(tmp_path, "a", []),
    ]
    monkeypatch.setattr(utils.glob, "glob", lambda *a, **k: paths)
    env = types.SimpleNamespace(loaded=[])

    # ACT — headless=True skips all Qt/UI feature registration; loadData still runs.
    loadModules(None, env, headless=True)

    # ASSERT — dependencies loaded before dependents, not in file order.
    assert env.loaded == ["a", "b", "c"]


def test_load_modules_aborts_and_logs_on_circular_dependency(
    tmp_path, monkeypatch, caplog
):
    # ARRANGE — a <-> b circular dependency.
    paths = [
        _write_plugin(tmp_path, "a", ["b"]),
        _write_plugin(tmp_path, "b", ["a"]),
    ]
    monkeypatch.setattr(utils.glob, "glob", lambda *a, **k: paths)
    env = types.SimpleNamespace(loaded=[])

    # ACT
    with caplog.at_level(logging.ERROR, logger="FFAST"):
        result = loadModules(None, env, headless=True)

    # ASSERT — loadModules bails out before loading any plugin's data and logs
    # the cycle (current behavior: no exception raised, returns None).
    assert result is None
    assert env.loaded == []
    assert any(
        "Cycle in module dependency graph" in r.getMessage() for r in caplog.records
    )
