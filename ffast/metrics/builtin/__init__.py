"""Built-in metrics.

Importing this package registers every built-in metric with the default
registry — each submodule's ``@metric`` decorators run on import. Previously the
submodules were imported piecemeal by different call sites (e.g. the client
imported force/energy/atomic but never accel), so some metrics were missing from
the registry depending on what had loaded. Importing the package guarantees the
full set.
"""
from ffast.metrics.builtin import (  # noqa: F401
    accel_metrics,
    atomic_metrics,
    energy_metrics,
    force_metrics,
    structure_metrics,
    transform_metrics,
)

from ffast.metrics.registry import default_registry as _registry  # noqa: E402

# Colourings every client lists: settings of the force and acceleration bases
# (ADR 0065 keep boxes). A box setting is made on demand, so these are made
# here to be listed with the built-ins.
LISTED_COLOURINGS = (
    "ffast.force_difference[atom+structure;length]",
    "ffast.force_difference[atom+structure;component;mae]",
    "ffast.force_difference[element;component;mae]",
    "ffast.force_difference[element;component;rmse]",
    "ffast.accel_difference[atom+structure;length]",
    "ffast.accel_difference[atom;length;mae]",
    "ffast.accel_difference[element;length;mae]",
)
for _id in LISTED_COLOURINGS:
    _registry.ensure(_id)
