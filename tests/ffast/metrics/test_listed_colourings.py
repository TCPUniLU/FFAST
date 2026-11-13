"""The force and acceleration colourings are listed with the built-ins
(ADR 0065 keep boxes): a box setting is otherwise made only when asked for,
so a client listing the metrics would not see them."""
import ffast.metrics.builtin as builtin
from ffast.metrics.registry import default_registry


def test_the_colourings_are_listed_with_the_built_ins():
    listed = set(default_registry.list_metrics())
    for metric_id in builtin.LISTED_COLOURINGS:
        assert metric_id in listed, metric_id
    assert default_registry.get("ffast.force_difference[atom+structure;length]")[0].label == (
        "Force Difference · per atom · length")
