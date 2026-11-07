"""A live run's analysis values as metrics under "Run scores" (ADR 0090).

Each value of an open run (one per test, the worst score, broken bonds,
too-close pairs) is a pass-through metric reading a ``run.*`` input ref
(``ffast.metrics.inputs``), plus a density of it for the density panel. They are
added to the registry while a run that has the value is open and removed when
no open run has it. Two runs scored with the same tests share the metrics: a
test of the same name means the same thing.
"""
from __future__ import annotations

import logging

from ffast.metrics import dims, units
from ffast.metrics.fields import field_passthrough
from ffast.metrics.inputs import parse_run_ref, run_ref, run_value_label
from ffast.metrics.registry import default_registry

logger = logging.getLogger("FFAST")

PREFIX = "ffast.run_"
GROUP = "Run scores"
DENSITY = "value_kde"
_DENSITY_SUFFIX = f"__{DENSITY}"


def metric_id(key) -> str:
    """``ffast.run_score_bonds_T2``, ``ffast.run_score_worst``, ``ffast.run_chem_broken``."""
    if key == ("worst",):
        return f"{PREFIX}score_worst"
    if key[0] == "chem":
        return f"{PREFIX}chem_{key[1]}"
    return f"{PREFIX}score_{key[1]}_{key[2]}"


def group_and_level(mid: str) -> tuple[str | None, float | None]:
    """The catalog group of a metric id and the level a panel draws a line at:
    1.0 for a ÷ threshold value (not its density), none for a count."""
    if not mid.startswith(PREFIX):
        return None, None
    level = 1.0 if mid.startswith(f"{PREFIX}score_") and not mid.endswith(_DENSITY_SUFFIX) else None
    return GROUP, level


def keys_of(run) -> list[tuple]:
    """The values a run can give: none before its first analysis, only the worst
    score for old (format 1) scores, else every test and both counts."""
    if not run.settings:
        return []
    keys: list[tuple] = [("worst",)]
    if run.settings.get("scores_format") == 2:
        keys += [("score", t[0], t[1]) for t in run.settings.get("tests") or []]
        keys += [("chem", "broken"), ("chem", "close")]
    return keys


def _add(registry, key) -> None:
    from ffast.metrics.transforms import compile_transform

    mid = metric_id(key)
    if registry.has(mid):
        return
    registry.metric(
        id=mid, label=run_value_label(key), inputs={"value": run_ref(key)},
        shape=(dims.N_frames,), unit=units.dimensionless,
    )(field_passthrough)
    compile_transform(mid, DENSITY, registry=registry)


def sync(env, registry=None) -> bool:
    """Make the registry list exactly the values the open runs have. Tells every
    window when the list changed. Returns whether it did."""
    registry = registry if registry is not None else default_registry
    wanted: dict[str, tuple] = {}
    for run in list((getattr(env, "live_runs", None) or {}).values()):
        for key in keys_of(run):
            wanted[metric_id(key)] = key
    have = {m for m in registry.list_metrics()
            if m.startswith(PREFIX) and not m.endswith(_DENSITY_SUFFIX)}
    gone = have - wanted.keys()
    added = [k for mid, k in wanted.items() if mid not in have]
    if not gone and not added:
        return False
    for mid in gone:
        for m in (mid + _DENSITY_SUFFIX, mid):
            if registry.has(m):
                registry.unregister(m)
    for key in added:
        _add(registry, key)
    errors = registry.freeze()
    if errors:
        logger.warning("Run scores: the metric table has errors: %s", errors)
    feed = getattr(env, "window_feed", None)
    if feed is not None:
        from ffast.protocol import control

        # The layout too: a saved tab's density of a run score could not be
        # compiled while no run had it (sent only if it changed).
        feed.changed(control.METRIC_CATALOG, control.TAB_LAYOUT)
    return True
