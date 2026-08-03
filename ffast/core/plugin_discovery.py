"""Unified plugin discovery + registration across three roots (ADR 0048).

Every plugin — a dataset loader, a model backend, or a Desktop Loupe
pane/tab — is a module declaring some subset of ``loadData(env)``,
``DEPENDENCIES``, ``CLIENT_FEATURES``, ``DATASET_FEATURES``. ``loadModules``
discovers plugins from three roots and registers them together in one
dependency-ordered pass:

1. **Bundled** ``ffast.plugins.{loaders,models}`` — real dotted-name import
   (``importlib.import_module``), always scanned. Ships inside the ``ffast``
   package itself, so a headless ``pip install ffast`` with no ``modules/``
   registers its dataset + model loaders. The bundled ML-backend plugins defer
   their actual framework import into the loader's ``__init__``/``predict``,
   so they register regardless of whether their extra is installed — a
   missing backend surfaces only when a user loads a model of that type, not
   here. A module whose own top-level import fails outright (a genuinely
   missing hard dependency) is skipped with a warning rather than crashing
   startup.
2. **Entry points** — third-party packages advertise plugins via
   ``importlib.metadata`` entry points in the ``ffast.loaders`` /
   ``ffast.models`` groups (e.g. a separately pip-installed ``ffast-mace``),
   so they self-register with no ``modules/`` and no core edit. Always
   available; a broken/uninstalled entry point is skipped the same way.
3. **`modules/` glob** — the Desktop-Client tree (Loupe panes, tab UI, and any
   local drop-in plugins), found by the pre-existing
   ``spec_from_file_location`` scan. Anchored at the flat repo root, not this
   file's own location, so it still finds a sibling ``modules/`` in a dev
   checkout; a real install ships no such directory and the glob simply finds
   nothing. On the desktop an import failure here is fatal (e.g. a genuinely
   broken pane); headless, it's a graceful skip (no Qt/display available).

Registering the same ``datasetName``/``modelName`` twice across any of the
three roots is an error, not a shadow — see
``Environment.initialiseModelType``/``initialiseDatasetType``, the single
choke point every plugin passes through regardless of which root discovered
it.
"""
import glob
import importlib
import importlib.util
import logging
import os
import pkgutil
from importlib.metadata import entry_points

logger = logging.getLogger("FFAST")

# ffast/core/plugin_discovery.py -> ffast/core -> ffast -> repo root. A real
# (non-dev-checkout) install has no sibling `modules/` dir there; the glob
# below then finds nothing, exactly like today.
_FLAT_ROOT = os.path.dirname(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
)
_MODULES_DIR = os.path.join(_FLAT_ROOT, "modules")

_BUNDLED_PACKAGES = ("ffast.plugins.loaders", "ffast.plugins.models")
_ENTRY_POINT_GROUPS = ("ffast.loaders", "ffast.models")


def kahnsAlgorithm(graph):
    degreeMap = {node: 0 for node in graph}

    for name, dependencies in graph.items():
        for dep in dependencies:
            degreeMap[dep] += 1

    queue = [node for node in graph if degreeMap[node] == 0]
    sortedNodes = []

    while queue:
        node = queue.pop(0)
        sortedNodes.append(node)

        for dep in graph[node]:
            degreeMap[dep] -= 1
            if degreeMap[dep] == 0:
                queue.append(dep)

    sortedNodes.reverse()
    if len(sortedNodes) == len(graph):
        return sortedNodes, degreeMap
    else:
        return None, degreeMap


def checkForInvalidDependencies(graph):
    validNodes = set(graph.keys())
    cleanedGraph = {}

    for node, dependencies in graph.items():
        valid = True
        for dep in dependencies:
            if dep not in validNodes:
                logger.error(
                    f"Module {node} cannot be loaded due to depending on inexistant module {dep}"
                )
                valid = False
        if valid:
            cleanedGraph[node] = dependencies

    if len(cleanedGraph) < len(graph):
        # need to cascade down if a node is removed
        return checkForInvalidDependencies(cleanedGraph)
    else:
        return cleanedGraph


def _register(mods, depGraph, name, mod):
    mods[name] = mod
    depGraph[name] = list(getattr(mod, "DEPENDENCIES", []))


def _discover_bundled(mods, depGraph):
    """Root 1: ``ffast.plugins.{loaders,models}``, real dotted-name import."""
    for pkg_name in _BUNDLED_PACKAGES:
        pkg = importlib.import_module(pkg_name)
        infos = sorted(
            pkgutil.iter_modules(pkg.__path__, prefix=pkg.__name__ + "."),
            key=lambda info: info.name,
        )
        for info in infos:
            if info.ispkg:
                continue
            try:
                mod = importlib.import_module(info.name)
            except Exception as exc:
                logger.warning(
                    "Skipping bundled plugin '%s' (import failed: %s)",
                    info.name, exc,
                )
                continue
            _register(mods, depGraph, info.name, mod)


def _discover_entry_points(mods, depGraph):
    """Root 2: third-party plugins advertised via ``importlib.metadata`` entry points."""
    for group in _ENTRY_POINT_GROUPS:
        for ep in entry_points(group=group):
            try:
                mod = ep.load()
            except Exception as exc:
                logger.warning(
                    "Skipping entry-point plugin '%s' (group=%s, import failed: %s)",
                    ep.name, group, exc,
                )
                continue
            _register(mods, depGraph, f"entry:{group}:{ep.name}", mod)


def _discover_desktop_glob(mods, depGraph, headless):
    """Root 3: the ``modules/`` glob (Desktop-Client tree), unchanged scan."""
    for path in glob.glob(os.path.join(_MODULES_DIR, "**", "*.py"), recursive=True):
        name = os.path.basename(path).replace(".py", "")

        spec = importlib.util.spec_from_file_location(f"module_{name}", path)
        mod = importlib.util.module_from_spec(spec)
        try:
            spec.loader.exec_module(mod)
        except Exception as exc:
            # A headless server has no display/Qt: client-feature plugins (e.g.
            # modules/loupe/* -> UI.Templates -> PySide6/libGL) can't import there.
            # Skip them rather than crash; their client features aren't needed
            # server-side. On the desktop, an import error is still fatal.
            if headless:
                logger.warning(
                    "Skipping plugin '%s' in headless mode (import failed: %s)",
                    name, exc,
                )
                continue
            raise

        _register(mods, depGraph, name, mod)


def loadModules(UI, env, headless=False):
    mods = {}
    depGraph = {}

    _discover_bundled(mods, depGraph)
    _discover_entry_points(mods, depGraph)
    _discover_desktop_glob(mods, depGraph, headless)

    # Drop dependencies on plugins that were skipped (import failures), so the
    # topological sort stays valid and never KeyErrors on a missing name.
    depGraph = {n: [d for d in deps if d in mods] for n, deps in depGraph.items()}
    depGraph = checkForInvalidDependencies(depGraph)
    order, degreeMap = kahnsAlgorithm(depGraph)
    if order is None:
        logger.error(
            f"Cycle in module dependency graph. Remaining nodes: {degreeMap}"
        )
        return

    for name in order:
        mod = mods[name]
        if hasattr(mod, "loadData"):
            mod.loadData(env)
        if (not headless) and hasattr(mod, "CLIENT_FEATURES"):
            UI.registerClientFeatures(mod.CLIENT_FEATURES)
        if (not headless) and hasattr(mod, "DATASET_FEATURES"):
            UI.registerDatasetFeatures(mod.DATASET_FEATURES)
            for feature in mod.DATASET_FEATURES:
                if feature.widget_factory is not None:
                    feature.widget_factory(UI, env)
