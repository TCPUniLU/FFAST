"""Where a metric step runs (ADR 0079)."""

from __future__ import annotations

import functools
from typing import Any


def is_outside_code(fn: Any) -> bool:
    """True when ``fn`` did not ship with FFAST: a project's metric module or
    a plugin outside the ``ffast`` package (CONTEXT.md, Outside code)."""
    while isinstance(fn, functools.partial):
        fn = fn.func
    module = getattr(fn, "__module__", None)
    if not isinstance(module, str):
        module = type(fn).__module__
    return not (module == "ffast" or module.startswith("ffast."))
