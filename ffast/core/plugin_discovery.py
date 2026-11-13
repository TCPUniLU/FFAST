"""Plugin discovery + registration across two roots (ADR 0048).

Every plugin — a dataset loader or a model backend — is a module declaring
``loadData(env)`` and optionally ``DEPENDENCIES``. ``loadModules`` discovers
plugins from two roots and registers them together in one dependency-ordered
pass:

1. **Bundled** ``ffast.plugins.{loaders,models}`` — real dotted-name import
   (``importlib.import_module``), always scanned. The bundled ML-backend
   plugins defer their actual framework import into the loader's
   ``__init__``/``predict``, so they register regardless of whether their
   extra is installed — a missing backend surfaces only when a user loads a
   model of that type, not here. A module whose own top-level import fails
   outright (a genuinely missing hard dependency) is skipped with a warning
   rather than crashing startup.
2. **Entry points** — third-party packages advertise plugins via
   ``importlib.metadata`` entry points in the ``ffast.loaders`` /
   ``ffast.models`` groups (e.g. a separately pip-installed ``ffast-mace``),
   so they self-register with no core edit. A broken/uninstalled entry point
   is skipped the same way.

(A third root, the desktop client's ``modules/`` folder, went with the
desktop client, ADR 0098.)

Registering the same ``datasetName``/``modelName`` twice across both roots is
an error, not a shadow — see
``Environment.initialiseModelType``/``initialiseDatasetType``, the single
choke point every plugin passes through regardless of which root discovered
it.
"""
import importlib
import logging
import pkgutil
from importlib.metadata import entry_points

logger = logging.getLogger("FFAST")

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


def loadModules(env):
    mods = {}
    depGraph = {}

    _discover_bundled(mods, depGraph)
    _discover_entry_points(mods, depGraph)

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
