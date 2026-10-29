import hashlib
import re
import sys
import numpy as np
import os
import logging
from collections.abc import Mapping

logger = logging.getLogger("FFAST")


def macosQtEnv(platform, environ, site_dirs):
    """The Qt variables main.py sets before importing PySide6, as a dict.

    macOS only: they point Qt at PySide6's own plugins and pick the cocoa
    display platform, which exists nowhere else. Nothing is set when the user
    set QT_PLUGIN_PATH, and a QT_QPA_PLATFORM the user chose (offscreen, say)
    is kept.
    """
    if platform != "darwin" or "QT_PLUGIN_PATH" in environ:
        return {}
    for site_dir in site_dirs:
        qt = os.path.join(site_dir, "PySide6", "Qt")
        plugins = os.path.join(qt, "plugins")
        if os.path.isdir(plugins):
            lib = os.path.join(qt, "lib")
            env = {
                "QT_PLUGIN_PATH": plugins,
                "QT_QPA_PLATFORM_PLUGIN_PATH": os.path.join(plugins, "platforms"),
                "DYLD_LIBRARY_PATH": lib,
                "DYLD_FRAMEWORK_PATH": lib,
            }
            if "QT_QPA_PLATFORM" not in environ:
                env["QT_QPA_PLATFORM"] = "cocoa"
            return env
    return {}


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
    from ffast.config.user import getConfig

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
