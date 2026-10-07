"""Which frames a plot's view covers (browser subbing).

While SUB is ticked on a browser plot, the frames inside the plot's visible
range are a live subset that follows the zoom, as on the desktop. The browser
sends the view; the server works out the parent's frames with the desktop's
rules (UI/panels.py ``PanelKind.sub_indices``). These tests run the rules on
plain arrays, with no environment.
"""
import numpy as np
import pytest

import ffast.metrics.builtin  # noqa: F401  (registers the metrics below)
from ffast.metrics.registry import default_registry
from ffast.session.subbing import (
    flat_to_frames, frames_in_view, frames_in_x_range, points_in_box,
    source_metric, values_in_range,
)


def test_a_timeline_covers_the_frames_on_screen():
    assert frames_in_x_range([2.3, 6.8], 10) == [3, 4, 5, 6]
    assert frames_in_x_range([6.8, 2.3], 10) == [3, 4, 5, 6]


def test_a_timeline_view_past_the_ends_stops_at_them():
    assert frames_in_x_range([-5, 2], 10) == [0, 1, 2]
    assert frames_in_x_range([8, 50], 10) == [8, 9]
    assert frames_in_x_range([20, 30], 10) == []


def test_a_scatter_covers_the_points_in_the_box():
    x = [0.0, 1.0, 2.0, 3.0]
    y = [0.0, 5.0, 1.0, 1.0]
    assert points_in_box(x, y, [0.5, 3.5], [0.5, 2]).tolist() == [2, 3]


def test_a_density_covers_the_frames_whose_value_is_in_range():
    """The density's x axis is |value|, so a negative value counts by size."""
    assert values_in_range([0.1, -0.4, 0.5, 0.9], [0.3, 0.6]).tolist() == [1, 2]


def test_a_shifted_density_filters_the_shifted_values():
    assert values_in_range([1.1, 1.4, 1.5, 1.9], [0.3, 0.6], shift=1.0).tolist() == [1, 2]


def test_force_components_map_to_their_frames():
    # Two atoms per frame, so six components per frame.
    assert flat_to_frames([0, 5, 6, 13], n_atoms=2).tolist() == [0, 1, 2]


def test_force_components_of_frames_of_different_sizes_map_to_their_frames():
    # Frames of 1, 2 and 1 atoms: components 0-2, 3-8, 9-11.
    assert flat_to_frames([2, 3, 8, 11], offsets=[0, 1, 3, 4]).tolist() == [0, 1, 2]


def test_a_density_reads_the_values_it_was_drawn_from():
    assert source_metric(default_registry, "ffast.force_mae_per_structure_density") \
        == "ffast.force_mae_per_structure"


def test_the_view_of_each_kind():
    values = {
        "e_true": np.array([0.0, 1.0, 2.0, 3.0]),
        "e_pred": np.array([0.0, 1.1, 2.4, 2.9]),
        "ffast.force_mae_per_structure": np.array([0.01, 0.2, 0.05, 0.3]),
    }

    def frames(view):
        return frames_in_view(view, n_frames=4, value_of=lambda mid, params: values.get(mid),
                              registry=default_registry)

    assert frames({"kind": "timeline", "metrics": {"y": "any"}, "x": [0.5, 2.5]}) == [1, 2]
    assert frames({"kind": "overlay_timeline", "metrics": {"series": ["a", "b"]},
                   "x": [2, 9]}) == [2, 3]
    assert frames({"kind": "scatter", "metrics": {"x": "e_true", "y": "e_pred"},
                   "x": [0.5, 3.5], "y": [0.5, 2.0]}) == [1]
    assert frames({"kind": "density",
                   "metrics": {"value": "ffast.force_mae_per_structure_density"},
                   "x": [0.1, 0.25]}) == [1]


def test_kinds_without_frames_cannot_make_a_subset():
    with pytest.raises(ValueError, match="table"):
        frames_in_view({"kind": "table", "metrics": {}, "x": [0, 1]}, n_frames=4,
                       value_of=lambda *_: None, registry=default_registry)
