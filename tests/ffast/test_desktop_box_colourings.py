"""The desktop has no keep boxes (ADR 0065 section 9): its Coloring list
offers fixed settings of the force and acceleration bases instead of the
deleted per-atom and per-element metrics."""
import importlib.util
import os

from ffast.metrics.registry import default_registry


def _load(name):
    root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    spec = importlib.util.spec_from_file_location(
        f"module_{name}_test", os.path.join(root, "modules", "loupe", f"{name}.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_the_coloring_list_offers_the_force_and_acceleration_settings():
    force_error = _load("loupeForceError")
    force_error.loadData(env=None)
    offered = _load("loupeAtoms")._atom_coloring_metric_ids(default_registry)
    for mid in force_error.DESKTOP_COLOURINGS:
        assert mid in offered, mid
    assert default_registry.get("ffast.force_difference[atom+structure;length]")[0].label == (
        "Force Difference · per atom · length")
