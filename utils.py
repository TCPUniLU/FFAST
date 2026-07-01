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


def cleanBondIdxsArray(arr):
    try:
        s = set()
        for x in arr:
            if x[0] == x[1]:
                continue
            elif x[0] < x[1]:
                s.add((x[0], x[1]))
            else:
                s.add((x[1], x[0]))

    except Exception as e:
        logger.exception(
            f"Tried to clean bond arr, but failed for: {e}. Array/List needs to be Nx2"
        )
        return False, None

    return True, list(s)


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
        spec.loader.exec_module(mod)

        mods[name] = mod
        if hasattr(mod, "DEPENDENCIES"):
            depGraph[name] = mod.DEPENDENCIES
        else:
            depGraph[name] = []

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


def md5FromArraysAndStrings(*args):
    fp = hashlib.md5()

    for arg in args:
        if isinstance(arg, str):
            d = arg.encode("utf8")
        elif isinstance(arg, np.ndarray):
            d = arg.ravel()
        elif isinstance(arg, list):
            # Handle list of arrays (variable-sized datasets)
            # Check if list contains numpy arrays
            if len(arg) > 0 and isinstance(arg[0], np.ndarray):
                # Flatten each array and concatenate
                d = np.concatenate([a.ravel() for a in arg])
            else:
                # Regular list - try to convert to array
                d = np.array(arg).ravel()

        fp.update(hashlib.md5(d).digest())

    return fp.hexdigest()


def removeExtension(path):
    if "." not in path:
        return path

    if path.startswith("."):
        return path.replace(".", "")

    match = re.match("^(.*)\.(.*)$", path)
    if match is None:
        return path.replace(".", "")
    else:
        return match.group(1).replace(".", "")


def rgbToHex(r, g, b):
    return "#{:02x}{:02x}{:02x}".format(r, g, b)


def hexToRGB(sHex):
    sHex = sHex.lstrip("#")

    r = int(sHex[0:2], 16)
    g = int(sHex[2:4], 16)
    b = int(sHex[4:6], 16)

    # return the RGB tuple as an array with values between 0 and 255
    return [r, g, b]


def mixColors(c1, c2):
    return np.array((np.array(c1) + np.array(c2)) / 2).astype(int)


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
