"""Unit tests for the unified plugin registrar (``ffast.core.plugin_discovery``,
ADR 0048) and its dependency-ordering primitives (``kahnsAlgorithm`` /
``checkForInvalidDependencies``).

``loadModules`` discovers plugins from three roots — bundled
``ffast.plugins.{loaders,models}`` (dotted-name import), third-party
``importlib.metadata`` entry points, and the Desktop-Client ``modules/`` glob —
and registers them together in one dependency-ordered pass. These tests cover
the pure ordering functions, each discovery root in isolation, end-to-end
``loadModules`` over fake root-3 plugin files (the historical
``test_load_modules.py`` coverage, relocated), a real end-to-end pass proving
the bundled ASE/ML-backend plugins register on a real ``Environment``, and the
duplicate-name registration guard that is the single choke point across all
three roots. ``NOTE``: distinct from ``test_module_loader.py``, which covers
the unrelated metric-config module loader (``load_metric_modules``).
"""
from __future__ import annotations

import logging
import types

import pytest

import ffast.core.plugin_discovery as plugin_discovery
from ffast.core.environment import Environment, HeadlessEnvironment, startHeadlessEnvironment
from ffast.core.plugin_discovery import (
    checkForInvalidDependencies,
    kahnsAlgorithm,
    loadModules,
)


def _ensure_event_loop():
    """Other suite tests may close/clear the process-global event loop; the
    headless Environment's TaskManager grabs asyncio.get_event_loop() at
    construction, which raises on a cleared loop. A real process always has
    one — ensure the same here so this test is order-independent."""
    import asyncio

    try:
        asyncio.get_event_loop()
    except RuntimeError:
        asyncio.set_event_loop(asyncio.new_event_loop())


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


# ── end-to-end: loadModules over fake root-3 (modules/ glob) plugin files ──
#
# Bundled (root 1) + entry-point (root 2) discovery is stubbed out here so
# these fakes are the only plugins in play — the ``env`` below is a bare
# SimpleNamespace with no initialiseDatasetType/initialiseModelType, which the
# real bundled plugins' loadData would need.

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
    monkeypatch.setattr(plugin_discovery.glob, "glob", lambda *a, **k: paths)
    monkeypatch.setattr(plugin_discovery, "_discover_bundled", lambda mods, depGraph: None)
    monkeypatch.setattr(plugin_discovery, "_discover_entry_points", lambda mods, depGraph: None)
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
    monkeypatch.setattr(plugin_discovery.glob, "glob", lambda *a, **k: paths)
    monkeypatch.setattr(plugin_discovery, "_discover_bundled", lambda mods, depGraph: None)
    monkeypatch.setattr(plugin_discovery, "_discover_entry_points", lambda mods, depGraph: None)
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


# ── root 1: bundled ffast.plugins.{loaders,models} ────────────────────────

def test_discover_bundled_skips_plugin_whose_import_fails(monkeypatch, caplog):
    # ARRANGE — inject one extra, deliberately-broken module name into the
    # real pkgutil scan of ffast.plugins.models (simulating an ML backend
    # whose extra isn't installed), alongside the real bundled plugins.
    real_iter_modules = plugin_discovery.pkgutil.iter_modules
    real_import_module = plugin_discovery.importlib.import_module
    broken_name = "ffast.plugins.models._fake_broken_backend"

    def fake_iter_modules(path, prefix):
        infos = list(real_iter_modules(path, prefix=prefix))
        if prefix == "ffast.plugins.models.":
            infos.append(types.SimpleNamespace(name=broken_name, ispkg=False))
        return infos

    def fake_import_module(name):
        if name == broken_name:
            raise ImportError("simulated missing extra")
        return real_import_module(name)

    monkeypatch.setattr(plugin_discovery.pkgutil, "iter_modules", fake_iter_modules)
    monkeypatch.setattr(plugin_discovery.importlib, "import_module", fake_import_module)

    mods, depGraph = {}, {}

    # ACT
    with caplog.at_level(logging.WARNING, logger="FFAST"):
        plugin_discovery._discover_bundled(mods, depGraph)

    # ASSERT — the broken plugin is skipped with a warning, not fatal; real
    # bundled plugins (e.g. the ASE loader) are still discovered alongside it.
    assert broken_name not in mods
    assert "ffast.plugins.loaders.ase" in mods
    assert any(broken_name in r.getMessage() for r in caplog.records)


# ── root 2: third-party plugins via importlib.metadata entry points ───────

def test_discover_entry_points_registers_and_skips_broken(monkeypatch, caplog):
    # ARRANGE — one entry point that loads fine, one whose target raises
    # (e.g. a separately pip-installed plugin package that's been uninstalled).
    class _FakeEntryPoint:
        def __init__(self, name, loader):
            self.name = name
            self._loader = loader

        def load(self):
            return self._loader()

    good_mod = types.SimpleNamespace(loadData=lambda env: None)

    def fake_entry_points(group):
        if group == "ffast.loaders":
            return [_FakeEntryPoint("good", lambda: good_mod)]
        if group == "ffast.models":
            def _boom():
                raise ImportError("third-party package uninstalled")
            return [_FakeEntryPoint("broken", _boom)]
        return []

    monkeypatch.setattr(plugin_discovery, "entry_points", fake_entry_points)

    mods, depGraph = {}, {}

    # ACT
    with caplog.at_level(logging.WARNING, logger="FFAST"):
        plugin_discovery._discover_entry_points(mods, depGraph)

    # ASSERT — keyed by "entry:<group>:<name>" so it can never collide with a
    # bundled dotted name or a modules/ glob basename.
    assert mods == {"entry:ffast.loaders:good": good_mod}
    assert any("broken" in r.getMessage() for r in caplog.records)


# ── real end-to-end: bundled plugins register on a real Environment ──────

def test_load_modules_registers_bundled_ase_and_ml_backend_plugins():
    # ARRANGE — a real headless Environment, no monkeypatching: proves the
    # actual ffast.plugins.{loaders,models} tree registers correctly, exactly
    # as server._main / startHeadlessEnvironment invoke it.
    _ensure_event_loop()
    env = Environment(headless=True)

    # ACT
    loadModules(None, env, headless=True)

    # ASSERT — the ASE smart loader and every ML-backend loader are
    # registered under their unchanged public names (session persistence
    # keys on these strings, so the ADR 0048 file move must not rename them).
    assert "ase (auto)" in env.datasetTypes
    for name in ("MACE", "Nequip", "SchNet", "SpookyNet", "sGDML"):
        assert name in env.modelTypes
    assert "sGDML" in env.datasetTypes


# ── startHeadlessEnvironment: no longer touches flat utils (ADR 0048) ─────

def test_start_headless_environment_configures_logging_without_flat_utils(monkeypatch):
    """startHeadlessEnvironment used to call utils.setupLogger — a flat-utils
    import that kept ffast/core/environment.py from clearing its last
    Environment -> utils edge (see test_ffast_core_boundary.py). It now
    configures stdlib logging directly instead; pin that this replacement
    actually configures logging (INFO level) rather than silently dropping it."""
    # Don't spin the real background thread or re-run the (already covered
    # above) full plugin registration — isolate this test to the bootstrap's
    # logging side effect.
    monkeypatch.setattr(HeadlessEnvironment, "start", lambda self: None)
    monkeypatch.setattr(plugin_discovery, "loadModules", lambda UI, env, headless=False: None)

    calls = []
    real_basic_config = logging.basicConfig
    monkeypatch.setattr(
        logging, "basicConfig",
        lambda *a, **k: (calls.append(k), real_basic_config(*a, **k))[-1],
    )

    # ACT
    env = startHeadlessEnvironment()

    # ASSERT
    assert isinstance(env, HeadlessEnvironment)
    assert len(calls) == 1
    assert calls[0]["level"] == logging.INFO
    assert calls[0].get("force") is True


# ── registration choke point: duplicate names are an error, not a shadow ──

def test_initialise_dataset_type_raises_on_duplicate_name():
    _ensure_event_loop()
    env = Environment(headless=True)

    class LoaderA:
        datasetName = "dup"

    class LoaderB:
        datasetName = "dup"

    env.initialiseDatasetType(LoaderA)
    with pytest.raises(ValueError, match="dup"):
        env.initialiseDatasetType(LoaderB)


def test_initialise_model_type_raises_on_duplicate_name():
    _ensure_event_loop()
    env = Environment(headless=True)

    class ModelA:
        modelName = "dup"

    class ModelB:
        modelName = "dup"

    env.initialiseModelType(ModelA)
    with pytest.raises(ValueError, match="dup"):
        env.initialiseModelType(ModelB)
