"""Modules imported for their stage-registration side effects.

``force_stages`` is absent: ADR 0049 deleted the ``ffast.force_arrows`` stage,
after which the module registered nothing and existed only to host the arrow
tessellation the Vispy adapter imported privately. ADR 0052 moved that to
``ffast/renderers/vispy/arrow_mesh.py``. Force arrows are still built — by
``scene_builder._build_forces`` from the ``ffast.force_arrows`` *parameter*
namespace, which was never a registered stage after 0049.
"""

from ffast.visualization.stages.builtin import (
    atom_stages,
    color_stages,
    geometry_stages,
    label_stages,
    selection_stages,
    transform_stages,
)

__all__ = [
    "atom_stages",
    "color_stages",
    "geometry_stages",
    "label_stages",
    "selection_stages",
    "transform_stages",
]
