"""Small pure helpers shared by the Headless Core (ADR 0047 Phase 5c).

Colour conversion, filename, and bond-index utilities the loaders and Environment
need, relocated out of the flat Desktop-Client ``utils.py`` so ``ffast/`` can use
them without importing the client spine. Pure (re / numpy / logging only).
"""
import logging
import re

import numpy as np

logger = logging.getLogger("FFAST")


def removeExtension(path):
    if "." not in path:
        return path

    if path.startswith("."):
        return path.replace(".", "")

    match = re.match(r"^(.*)\.(.*)$", path)
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
