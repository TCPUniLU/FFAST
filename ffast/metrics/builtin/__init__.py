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
