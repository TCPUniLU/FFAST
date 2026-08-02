import hashlib
import re
import sys
import numpy as np
import glob
import importlib
import os
import logging
from collections.abc import Mapping

logger = logging.getLogger("FFAST")


def setupLogger(level=logging.INFO):
    logging.basicConfig(
        level=level,
        format="[%(levelname)s] %(message)s",
        handlers=[
            logging.FileHandler(
                os.path.join(os.path.dirname(os.path.abspath(__file__)), "debug.log")
            ),
            logging.StreamHandler(),
        ],
        force=True,  # install our handlers even if logging was already configured
    )


def deep_getsizeof(obj, seen=None):
    """
    Function to calculate the size of an object on memory in bites.
    :param obj: Desired object
    :param seen: Flag to avoid double counting of objects.
    :return: Size of the object in bytes.
    """
    if seen is None:
        seen = set()

    obj_id = id(obj)
    if obj_id in seen:
        return 0
    seen.add(obj_id)

    size = sys.getsizeof(obj)

    if isinstance(obj, Mapping):
        size += sum(deep_getsizeof(k, seen) + deep_getsizeof(v, seen) for k, v in obj.items())
    elif isinstance(obj, (list, tuple, set, frozenset)):
        size += sum(deep_getsizeof(i, seen) for i in obj)
    elif hasattr(obj, "__dict__"):
        size += deep_getsizeof(obj.__dict__, seen)
    elif hasattr(obj, "__slots__"):
        for slot in obj.__slots__:
            if hasattr(obj, slot):
                size += deep_getsizeof(getattr(obj, slot), seen)

    return size


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


def loadModules(UI, env, headless=False):
    mods = {}
    depGraph = {}

    _root = os.path.dirname(os.path.abspath(__file__))
    # modules/ is organised into typed sub-packages (loupe/, loaders/, tabs/),
    # so the discovery glob recurses. Module identity stays the basename: every
    # plugin file has a unique basename across the tree.
    for path in glob.glob(
        os.path.join(_root, "modules", "**", "*.py"), recursive=True
    ):
        name = os.path.basename(path).replace(".py", "")

        spec = importlib.util.spec_from_file_location(f"module_{name}", path)
        mod = importlib.util.module_from_spec(spec)
        try:
            spec.loader.exec_module(mod)
        except Exception as exc:
            # A headless server has no display/Qt: client-feature plugins (e.g.
            # modules/loupe/* → UI.Templates → PySide6/libGL) can't import there.
            # Skip them rather than crash; their client features aren't needed
            # server-side. On the desktop, an import error is still fatal.
            if headless:
                logger.warning(
                    "Skipping plugin '%s' in headless mode (import failed: %s)",
                    name, exc,
                )
                continue
            raise

        mods[name] = mod
        if hasattr(mod, "DEPENDENCIES"):
            depGraph[name] = mod.DEPENDENCIES
        else:
            depGraph[name] = []

    # Drop dependencies on plugins that were skipped (headless import failures),
    # so the topological sort stays valid and never KeyErrors on a missing name.
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


# Relocated to ffast.cache.fingerprint (ADR 0047); re-exported here so the
# flat/loader call sites keep working until Phase 6 repoints them.
from ffast.cache.fingerprint import md5FromArraysAndStrings  # noqa: E402,F401


# Relocated to ffast.core.util (ADR 0047 Phase 5c); re-exported so flat/UI call
# sites keep working until Phase 6 repoints them.
from ffast.core.util import (  # noqa: E402,F401
    cleanBondIdxsArray,
    hexToRGB,
    mixColors,
    removeExtension,
    rgbToHex,
)


def _kde_xy(sample, bounds):
    """Shared skeleton for the error/distribution KDE plots → ``(x, y)``.

    ``sample`` is the 1-D array fed to the KDE; ``bounds(sample) -> (lo, hi)``
    gives the x-grid extent and is evaluated only for the non-degenerate case.
    Degenerate input (fewer than two points or ~zero spread) returns a unit
    spike at the mean over ``[0, max]`` — identical to the per-module copies
    this replaces. Number of grid points comes from the ``plotDistNum`` config.
    """
    from scipy.stats import gaussian_kde
    from config.userConfig import getConfig

    sample = np.asarray(sample, dtype=np.float64).ravel()
    nPts = getConfig("plotDistNum")
    if len(sample) < 2 or np.std(sample) < 1e-10:
        top = max(np.max(sample), 1e-10) if len(sample) else 1e-10
        x = np.linspace(0, top, nPts)
        y = np.zeros_like(x)
        if len(sample):
            y[np.argmin(np.abs(x - np.mean(sample)))] = 1.0
        return x, y
    kde = gaussian_kde(sample)
    lo, hi = bounds(sample)
    x = np.linspace(lo, hi, nPts)
    return x, kde(x)


def mirrorKDE(values):
    """Symmetric-mirror KDE of ``|values|`` over ``[0, max·1.05]`` → ``(x, y)``.

    Error-distribution shape for the force/energy error panels: reflect the
    absolute errors about zero so the density is symmetric and starts at 0.
    """
    a = np.abs(np.asarray(values, dtype=np.float64).ravel())
    return _kde_xy(np.concatenate([a, -a]), lambda s: (0.0, np.max(s) * 1.05))


def absKDE(values):
    """KDE of ``|values|`` (not mirrored) over ``[min·0.95, max·1.05]``.

    The per-element atomic-error distribution shape.
    """
    a = np.abs(np.asarray(values, dtype=np.float64).ravel())
    return _kde_xy(a, lambda s: (np.min(s) * 0.95, np.max(s) * 1.05))


def valueKDE(values):
    """KDE of raw ``values`` over ``[min, max]`` padded 5% each side.

    Distribution shape for non-error quantities (e.g. gyration radius) that are
    neither absolute-valued nor mirrored.
    """
    v = np.asarray(values, dtype=np.float64).ravel()

    def bounds(s):
        delta = np.max(s) - np.min(s)
        return (np.min(s) - delta * 0.05, np.max(s) + delta * 0.05)

    return _kde_xy(v, bounds)
